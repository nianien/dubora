"""
Manifest: phase status tracking + artifact path resolution.

Artifact paths are computed dynamically from key → workspace-relative path.
No DB storage for artifacts — paths are deterministic.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dubora.pipeline.core.types import ErrorInfo, Status


# ── Artifact path resolution ──────────────────────────────────

def resolve_artifact_path(key: str, workspace: Path) -> Path:
    """
    根据 artifact key 解析最终文件路径。

    Args:
        key: artifact key（如 "extract.audio"）
        workspace: workspace 根目录

    Returns:
        最终文件路径（绝对路径）
    """
    parts = key.split(".", 1)
    if len(parts) == 2:
        domain, obj = parts
    else:
        domain = "misc"
        obj = key

    # 路径映射规则（workspace-relative）
    # 按资产生命周期分层：
    #   input/   — 不可变，创建后不修改
    #   derived/ — 可重算的中间产物
    #   output/  — 最终交付物
    path_map = {
        "extract": {
            "audio": "input/{episode_stem}.wav",
            "vocals": "input/{episode_stem}-vocals.wav",
            "accompaniment": "input/{episode_stem}-accompaniment.wav",
        },
        "asr": {
            "asr_result": "input/asr-result.json",
        },
        "subs": {
            "zh_srt": "output/zh.srt",
            "en_srt": "output/en.srt",
        },
        "tts": {
            "segments_dir": "derived/tts/segments",
        },
        "mix": {
            "audio": "derived/{episode_stem}-mix.wav",
        },
        "burn": {
            "video": "output/{episode_stem}-dubbed.mp4",
        },
    }

    if domain in path_map and obj in path_map[domain]:
        path_template = path_map[domain][obj]
    else:
        path_template = f"{domain}/{obj}"

    episode_stem = workspace.name
    path_str = path_template.format(episode_stem=episode_stem)

    return workspace / path_str


def now_iso() -> str:
    """返回当前时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


# ── DbManifest ─────────────────────────────────────────────────

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
