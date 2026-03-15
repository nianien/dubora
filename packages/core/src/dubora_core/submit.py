"""
Pipeline submission and reaction: write tasks to DB, advance pipeline on events.

Extracted from worker.py so that web (lightweight) can submit tasks
without importing heavy pipeline execution code.

Both submit_pipeline() and PipelineReactor accept phase_names: list[str]
instead of Phase instances — no need for lazy-loaded phase objects.
"""
from __future__ import annotations

from typing import Optional

from dubora_core.events import EventEmitter, PipelineEvent
from dubora_core.store import DbStore


def _active_range(
    phase_names: list[str],
    from_phase: Optional[str],
    to_phase: Optional[str],
) -> list[str]:
    """Return the sub-list of phases between from_phase and to_phase (inclusive)."""
    start = 0
    end = len(phase_names) - 1

    if from_phase:
        if from_phase not in phase_names:
            raise ValueError(f"Unknown phase: {from_phase}")
        start = phase_names.index(from_phase)

    if to_phase:
        if to_phase not in phase_names:
            raise ValueError(f"Unknown phase: {to_phase}")
        end = phase_names.index(to_phase)

    if start > end:
        raise ValueError(f"--from ({from_phase}) must be before --to ({to_phase})")

    return phase_names[start:end + 1]


# --------------------------------------------------------------------------- #
# Submit: write tasks to DB
# --------------------------------------------------------------------------- #

def submit_pipeline(
    store: DbStore,
    episode_id: int,
    phase_names: list[str],
    gate_after: dict,
    *,
    from_phase: Optional[str] = None,
    to_phase: Optional[str] = None,
) -> None:
    """Submit a pipeline: create the first task, set episode to running.

    When from_phase is None (resume), derives next phase from tasks table.
    """
    active = _active_range(phase_names, from_phase, to_phase)
    force = from_phase is not None

    # Idempotency: skip if already has pending/running tasks
    latest = store.get_latest_task(episode_id)
    if latest and latest["status"] in ("pending", "running"):
        if force:
            # Explicit rerun: cancel pending tasks (e.g. gate tasks) to allow backtracking
            store.delete_pending_tasks(episode_id)
        else:
            return

    if from_phase is not None:
        first = active[0]
    else:
        # Resume: derive next phase from tasks
        if not latest:
            first = active[0]
        elif latest["status"] == "failed" and latest["type"] in active:
            # Retry failed phase
            first = latest["type"]
        else:
            # Find last succeeded phase in active range
            tasks = store.get_tasks(episode_id)
            last_succeeded = None
            for t in reversed(tasks):
                if t["status"] == "succeeded" and t["type"] in active:
                    last_succeeded = t["type"]
                    break
            if last_succeeded:
                # Gate check: if there's an unpassed gate, create gate task
                gate_def = gate_after.get(last_succeeded)
                if gate_def:
                    gate_key = gate_def["key"]
                    gate_task = store.get_gate_task(episode_id, gate_key)
                    if not (gate_task and gate_task["status"] == "succeeded"):
                        if not gate_task or gate_task["status"] != "pending":
                            store.create_task(episode_id, gate_key)
                        store.update_episode_status(episode_id, "review")
                        return

                idx = active.index(last_succeeded)
                if idx + 1 >= len(active):
                    return  # All done
                first = active[idx + 1]
            else:
                first = active[0]

    context = {
        "force": force,
        "from_phase": from_phase,
        "to_phase": to_phase,
    }
    store.create_task(episode_id, first, context=context)
    store.update_episode_status(episode_id, "running")


# --------------------------------------------------------------------------- #
# Reactor: event listener that creates next tasks
# --------------------------------------------------------------------------- #

class PipelineReactor:
    """
    Listens to task events on the in-memory EventEmitter.
    Creates next tasks in the DB based on pipeline structure.
    """

    def __init__(
        self,
        store: DbStore,
        emitter: EventEmitter,
        episode_id: int,
        phase_names: list[str],
        gate_after: dict,
        *,
        from_phase: Optional[str] = None,
        to_phase: Optional[str] = None,
    ):
        self.store = store
        self.emitter = emitter
        self.episode_id = episode_id
        self.phase_names = phase_names
        self.gate_after = gate_after
        self.gate_keys = {g["key"] for g in gate_after.values()}
        self.active = _active_range(self.phase_names, from_phase, to_phase)
        self.from_phase = from_phase
        self.to_phase = to_phase

    def __call__(self, event: PipelineEvent) -> None:
        if event.kind == "task_succeeded":
            self._on_succeeded(event)
        elif event.kind == "task_failed":
            self._on_failed(event)

    def _on_succeeded(self, event: PipelineEvent) -> None:
        task_type = event.data.get("type", "")

        if task_type in self.gate_keys:
            after_phase = self._phase_before_gate(task_type)
            if after_phase:
                next_name = self._next_in_active(after_phase)
                if next_name:
                    self._enqueue(next_name)
                    self.store.update_episode_status(self.episode_id, "running")
                else:
                    self._pipeline_done()
            return

        next_name = self._next_in_active(task_type)
        if next_name is None:
            self._pipeline_done()
            return

        gate_def = self.gate_after.get(task_type)
        if gate_def:
            gate_key = gate_def["key"]
            existing = self.store.get_gate_task(self.episode_id, gate_key)
            if existing and existing["status"] == "succeeded" and not self.from_phase:
                self._enqueue(next_name)
                return
            self.store.create_task(self.episode_id, gate_key)
            self.store.update_episode_status(self.episode_id, "review")
            self.emitter.emit(PipelineEvent(
                kind="gate_awaiting",
                run_id=str(self.episode_id),
                data={"gate": gate_key},
            ))
        else:
            self._enqueue(next_name)

    def _on_failed(self, event: PipelineEvent) -> None:
        self.store.update_episode_status(self.episode_id, "failed")
        self.emitter.emit(PipelineEvent(
            kind="pipeline_failed",
            run_id=str(self.episode_id),
            data=event.data,
        ))

    def _enqueue(self, phase_name: str) -> None:
        # Check if episode was stopped (pending tasks deleted)
        ep = self.store.get_episode(self.episode_id)
        if ep and ep["status"] not in ("running", "review"):
            return
        force = self._should_force(phase_name)
        self.store.create_task(self.episode_id, phase_name, context={
            "force": force,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
        })

    def _pipeline_done(self) -> None:
        self.store.update_episode_status(self.episode_id, "succeeded")
        self.emitter.emit(PipelineEvent(
            kind="pipeline_done",
            run_id=str(self.episode_id),
        ))

    def _next_in_active(self, current: str) -> Optional[str]:
        try:
            idx = self.active.index(current)
        except ValueError:
            return None
        return self.active[idx + 1] if idx + 1 < len(self.active) else None

    def _phase_before_gate(self, gate_key: str) -> Optional[str]:
        for phase_name, gate_def in self.gate_after.items():
            if gate_def["key"] == gate_key:
                return phase_name
        return None

    def _should_force(self, phase_name: str) -> bool:
        if not self.from_phase:
            return False
        try:
            from_idx = self.phase_names.index(self.from_phase)
            phase_idx = self.phase_names.index(phase_name)
            return phase_idx >= from_idx
        except ValueError:
            return False
