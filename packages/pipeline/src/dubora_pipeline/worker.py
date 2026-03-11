"""
Pipeline task execution: poll DB for pending tasks and execute them.

submit_pipeline and PipelineReactor live in submit.py (lightweight, DB-only).
This module contains PipelineWorker (heavy, requires phase implementations).

Re-exports for backward compatibility:
    from dubora_pipeline.worker import submit_pipeline, PipelineReactor
"""
from __future__ import annotations

import json
import time
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from dubora_core.config.settings import PipelineConfig, get_workdir as _settings_get_workdir, get_upload_cache_dir, get_gcs_cache_dir
from dubora_core.events import EventEmitter, LogListener, PipelineEvent
from dubora_pipeline.manifest import DbManifest
from dubora_pipeline.runner import PhaseRunner
from dubora_core.store import DbStore
from dubora_core.submit import PipelineReactor, submit_pipeline
from dubora_pipeline.types import RunContext
from dubora_core.utils.logger import info as log_info, error as log_error


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
        from dubora_core.utils.file_store import _gcs_bucket
        blob = _gcs_bucket().blob(blob_path)
        gcs_local.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(gcs_local))
        if gcs_local.is_file():
            return gcs_local
    except Exception:
        pass

    return None


def _parse_context(task: dict) -> dict:
    ctx = task.get("context", "{}")
    if isinstance(ctx, str):
        return json.loads(ctx)
    return ctx or {}


# --------------------------------------------------------------------------- #
# Worker: global task consumer
# --------------------------------------------------------------------------- #

class PipelineWorker:
    """
    Global worker: polls DB for any pending task, executes it.
    Stateless per-tick — each task carries its own context (from_phase, to_phase, force).

    Supports two modes:
    - Local mode (store=DbStore): direct DB access + local reactor
    - Remote mode (store=RemoteStore): HTTP API access, reactor runs on web side
    """

    def __init__(
        self,
        store,
        phases: list,
        gate_after: dict,
        *,
        remote: bool = False,
    ):
        self.store = store
        self.phases = {p.name: p for p in phases}
        self.phase_names = [p.name for p in phases]
        self.all_phases = phases
        self.gate_after = gate_after
        self.remote = remote

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

        # In remote mode, inform RemoteStore of current task for complete/fail
        if self.remote:
            self.store.set_current_task(task, task_ctx)

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

        # Local mode: set up emitter + reactor for task advancement
        # Remote mode: reactor runs on web side (via /complete and /fail endpoints)
        emitter = EventEmitter()
        emitter.on(LogListener())
        if not self.remote:
            from_phase = task_ctx.get("from_phase")
            to_phase = task_ctx.get("to_phase")
            reactor = PipelineReactor(
                self.store, emitter, episode_id, self.phase_names, self.gate_after,
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
