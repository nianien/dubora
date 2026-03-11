"""
DbManifest: DB-backed phase status tracker.

Artifact paths are computed via resolve_artifact_path (in dubora_core.manifest).
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from dubora_pipeline.types import ErrorInfo, Status


class DbManifest:
    """DB-backed phase status tracker. Artifact paths are computed, not stored."""

    def __init__(self, store, episode_id: int, workspace: Path):
        self.store = store
        self.episode_id = episode_id
        self.workspace = workspace
        self._current_task_id: Optional[int] = None

    def set_current_task(self, task_id: int) -> None:
        """Set the task that update_phase writes to."""
        self._current_task_id = task_id

    def save(self) -> None:
        """DB operations are immediate; no-op for compatibility."""
        pass

    def get_phase_data(self, phase_name: str) -> Optional[Dict[str, Any]]:
        """Get phase data from the latest succeeded task."""
        task = self.store.get_latest_succeeded_task(self.episode_id, phase_name)
        if task is None:
            return None
        ctx = json.loads(task["context"] or "{}")
        return {
            "name": phase_name,
            "version": ctx.get("version"),
            "status": task["status"],
            "started_at": task.get("claimed_at"),
            "finished_at": task.get("finished_at"),
            "error": task.get("error"),
            "skipped": ctx.get("skipped", False),
            "metrics": ctx.get("metrics", {}),
        }

    def update_phase(
        self,
        phase_name: str,
        *,
        version: str,
        status: Status,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        requires: Optional[List[str]] = None,
        provides: Optional[List[str]] = None,
        config_fingerprint: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
        warnings: Optional[List[str]] = None,
        error: Optional[ErrorInfo] = None,
        skipped: Optional[bool] = None,
    ) -> None:
        if self._current_task_id is not None:
            updates: Dict[str, Any] = {"version": version}
            if requires is not None:
                updates["requires"] = requires
            if provides is not None:
                updates["provides"] = provides
            if config_fingerprint:
                updates["config_fingerprint"] = config_fingerprint
            if metrics:
                updates["metrics"] = metrics
            if skipped is not None:
                updates["skipped"] = skipped
            if error:
                updates["error_detail"] = {
                    "type": error.type,
                    "message": error.message,
                    "traceback": error.traceback,
                }
            self.store.update_task_context(self._current_task_id, updates)
