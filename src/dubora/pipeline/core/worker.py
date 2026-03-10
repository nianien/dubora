"""
Pipeline task execution: submit, react, and work.

Architecture:
  submit_pipeline() → write first task to DB, exit
  PipelineReactor   → on task_succeeded, create next task in DB
  PipelineWorker    → global worker, polls DB for any pending task, executes it

  Separation: submitter (CLI/Web) only writes DB.
              Worker (standalone or ide thread) only reads+executes.
"""
from __future__ import annotations

import json
import time
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from dubora.config.settings import PipelineConfig, get_workdir as _settings_get_workdir, get_upload_cache_dir, get_gcs_cache_dir
from dubora.pipeline.core.events import EventEmitter, LogListener, PipelineEvent
from dubora.pipeline.core.manifest import DbManifest
from dubora.pipeline.core.runner import PhaseRunner
from dubora.pipeline.core.store import PipelineStore
from dubora.pipeline.core.types import RunContext
from dubora.utils.logger import info as log_info, error as log_error


def _get_workdir(drama_name: str, episode_number: int) -> Path:
    """Derive workdir from drama_name + episode_number."""
    return _settings_get_workdir(drama_name, episode_number)


def _resolve_video_path(episode: dict) -> Path | None:
    """Resolve source video path by priority:
    1. uploads/{blob_path} — freshly uploaded
    2. gcs/{blob_path} — GCS download cache
    3. Download from GCS → gcs/{blob_path}
    """
    blob_path = episode.get("path")
    if not blob_path:
        return None

    # 1) Upload cache
    upload = get_upload_cache_dir() / blob_path
    if upload.is_file():
        return upload

    # 2) GCS download cache
    gcs_local = get_gcs_cache_dir() / blob_path
    if gcs_local.is_file():
        return gcs_local

    # 3) Download from GCS
    try:
        from dubora.utils.file_store import _gcs_bucket
        blob = _gcs_bucket().blob(blob_path)
        gcs_local.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(gcs_local))
        if gcs_local.is_file():
            return gcs_local
    except Exception:
        pass

    return None


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


def _parse_context(task: dict) -> dict:
    ctx = task.get("context", "{}")
    if isinstance(ctx, str):
        return json.loads(ctx)
    return ctx or {}


# --------------------------------------------------------------------------- #
# Submit: write tasks to DB
# --------------------------------------------------------------------------- #

def submit_pipeline(
    store: PipelineStore,
    episode_id: int,
    phases: list,
    gate_after: dict,
    *,
    from_phase: Optional[str] = None,
    to_phase: Optional[str] = None,
) -> None:
    """Submit a pipeline: create the first task, set episode to running.

    When from_phase is None (resume), derives next phase from tasks table.
    """
    phase_names = [p.name for p in phases]
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
        store: PipelineStore,
        emitter: EventEmitter,
        episode_id: int,
        phases: list,
        gate_after: dict,
        *,
        from_phase: Optional[str] = None,
        to_phase: Optional[str] = None,
    ):
        self.store = store
        self.emitter = emitter
        self.episode_id = episode_id
        self.phase_names = [p.name for p in phases]
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
            if existing and existing["status"] == "succeeded":
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


# --------------------------------------------------------------------------- #
# Worker: global task consumer
# --------------------------------------------------------------------------- #

class PipelineWorker:
    """
    Global worker: polls DB for any pending task, executes it.
    Stateless per-tick — each task carries its own context (from_phase, to_phase, force).
    """

    def __init__(
        self,
        store: PipelineStore,
        phases: list,
        gate_after: dict,
    ):
        self.store = store
        self.phases = {p.name: p for p in phases}
        self.phase_names = [p.name for p in phases]
        self.all_phases = phases
        self.gate_after = gate_after

    def tick(self) -> bool:
        """Claim one pending task, execute it. Returns True if work was done."""
        task = self.store.claim_any_pending_task(
            executable_types=self.phase_names,
        )
        if not task:
            return False

        episode_id = task["episode_id"]
        task_type = task["type"]
        task_ctx = _parse_context(task)

        # Set up per-episode execution context
        episode = self.store.get_episode(episode_id)
        log_info(f"Task claimed: {task_type} (episode={episode['drama_name']}/{episode['number']}, task_id={task['id']})")
        workdir = _get_workdir(episode["drama_name"], episode["number"])
        workdir.mkdir(parents=True, exist_ok=True)
        video_path = _resolve_video_path(episode)

        manifest = DbManifest(self.store, episode_id, workdir)
        manifest.set_current_task(task["id"])
        runner = PhaseRunner(manifest, workdir)

        config = PipelineConfig()
        config_dict = asdict(config)
        config_dict["video_path"] = str(video_path.absolute()) if video_path else ""
        config_dict["phases"] = {}
        ctx = RunContext(
            job_id=str(episode_id),
            workspace=str(workdir),
            config=config_dict,
            store=self.store,
            episode_id=episode_id,
        )

        # Per-task emitter + reactor
        emitter = EventEmitter()
        emitter.on(LogListener())
        from_phase = task_ctx.get("from_phase")
        to_phase = task_ctx.get("to_phase")
        reactor = PipelineReactor(
            self.store, emitter, episode_id, self.all_phases, self.gate_after,
            from_phase=from_phase, to_phase=to_phase,
        )
        emitter.on(reactor)

        phase = self.phases[task_type]

        emitter.emit(PipelineEvent(
            kind=f"{task_type}_start",
            run_id=str(episode_id),
            phase=task_type,
        ))

        try:
            success, err_msg = runner.run_phase(
                phase, ctx, force=bool(task_ctx.get("force", False)),
            )
        except Exception as e:
            log_error(f"Task exception: {task_type} (task_id={task['id']}): {e}")
            self.store.fail_task(task["id"], error=str(e))
            emitter.emit(PipelineEvent(
                kind="task_failed",
                run_id=str(episode_id),
                phase=task_type,
                data={"type": task_type, "error": str(e),
                      "traceback": traceback.format_exc()},
            ))
            return True

        if success:
            log_info(f"Task succeeded: {task_type} (task_id={task['id']})")
            self.store.complete_task(task["id"])
            emitter.emit(PipelineEvent(
                kind=f"{task_type}_done",
                run_id=str(episode_id),
                phase=task_type,
            ))
            emitter.emit(PipelineEvent(
                kind="task_succeeded",
                run_id=str(episode_id),
                phase=task_type,
                data={"type": task_type},
            ))
        else:
            fail_msg = err_msg or f"Phase '{task_type}' failed"
            log_error(f"Task failed: {task_type} (task_id={task['id']}): {fail_msg}")
            self.store.fail_task(task["id"], error=fail_msg)
            emitter.emit(PipelineEvent(
                kind=f"{task_type}_failed",
                run_id=str(episode_id),
                phase=task_type,
                data={"type": task_type, "error": fail_msg},
            ))
            emitter.emit(PipelineEvent(
                kind="task_failed",
                run_id=str(episode_id),
                phase=task_type,
                data={"type": task_type, "error": fail_msg},
            ))

        return True

    def run_forever(self, stop_event: Optional[threading.Event] = None) -> None:
        """Poll DB and execute tasks until stopped."""
        while True:
            if stop_event and stop_event.is_set():
                break
            did_work = self.tick()
            if not did_work:
                time.sleep(0.5)
