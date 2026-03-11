"""
RemoteStore: HTTP proxy implementing the same interface as DbStore.

Used by PipelineWorker when running on a separate machine from the web server.
Each method maps to a Worker API endpoint on the web server.
"""
from __future__ import annotations

from typing import Optional

import requests


class RemoteStore:
    """HTTP proxy for DbStore. Same method signatures, each call hits the Worker API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"
        # Set by worker after claiming a task, used by complete_task/fail_task
        self._current_task: Optional[dict] = None
        self._task_context: dict = {}

    def set_current_task(self, task: dict, context: dict) -> None:
        """Set the current task context (called by worker after claim)."""
        self._current_task = task
        self._task_context = context

    # ── HTTP helpers ─────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api{path}"

    def _get(self, path: str, **kwargs) -> requests.Response:
        r = self._session.get(self._url(path), **kwargs)
        r.raise_for_status()
        return r

    def _post(self, path: str, **kwargs) -> requests.Response:
        r = self._session.post(self._url(path), **kwargs)
        r.raise_for_status()
        return r

    def _patch(self, path: str, **kwargs) -> requests.Response:
        r = self._session.patch(self._url(path), **kwargs)
        r.raise_for_status()
        return r

    # ── Task lifecycle (worker-specific) ─────────────────────────

    def claim_any_pending_task(self, *, executable_types: list[str]) -> Optional[dict]:
        r = self._post("/worker/claim", json={"executable_types": executable_types})
        return r.json().get("task")

    def complete_task(self, task_id: int) -> None:
        task = self._current_task or {}
        self._post(f"/worker/tasks/{task_id}/complete", json={
            "episode_id": task.get("episode_id"),
            "task_type": task.get("type"),
            "from_phase": self._task_context.get("from_phase"),
            "to_phase": self._task_context.get("to_phase"),
        })

    def fail_task(self, task_id: int, *, error: Optional[str] = None) -> None:
        task = self._current_task or {}
        self._post(f"/worker/tasks/{task_id}/fail", json={
            "episode_id": task.get("episode_id"),
            "task_type": task.get("type"),
            "error": error,
            "from_phase": self._task_context.get("from_phase"),
            "to_phase": self._task_context.get("to_phase"),
        })

    # ── Episodes ─────────────────────────────────────────────────

    def get_episode(self, episode_id: int) -> Optional[dict]:
        try:
            r = self._get(f"/worker/episodes/{episode_id}")
            return r.json().get("episode")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ── Drama ────────────────────────────────────────────────────

    def get_drama_synopsis(self, drama_id: int) -> str:
        r = self._get(f"/worker/dramas/{drama_id}/synopsis")
        return r.json().get("synopsis", "")

    # ── Cues ─────────────────────────────────────────────────────

    def has_cues(self, episode_id: int) -> bool:
        r = self._get(f"/worker/episodes/{episode_id}/has-cues")
        return r.json().get("has_cues", False)

    def get_cues(self, episode_id: int) -> list[dict]:
        r = self._get(f"/worker/episodes/{episode_id}/cues")
        return r.json().get("cues", [])

    def get_cues_for_utterance(self, utterance_id: int) -> list[dict]:
        # Use episode_id=0 as placeholder; endpoint uses utterance_id query param
        r = self._get("/worker/episodes/0/cues", params={"utterance_id": utterance_id})
        return r.json().get("cues", [])

    def insert_cues(self, episode_id: int, rows: list[dict]) -> list[int]:
        r = self._post(f"/worker/episodes/{episode_id}/cues/insert", json={"cues": rows})
        return r.json().get("cue_ids", [])

    def update_cue(self, cue_id: int, **fields) -> None:
        self._patch(f"/worker/cues/{cue_id}", json=fields)

    def delete_episode_cues(self, episode_id: int) -> int:
        r = self._session.delete(self._url(f"/worker/episodes/{episode_id}/cues"))
        r.raise_for_status()
        return r.json().get("deleted", 0)

    def delete_episode_utterances(self, episode_id: int) -> int:
        r = self._session.delete(self._url(f"/worker/episodes/{episode_id}/utterances"))
        r.raise_for_status()
        return r.json().get("deleted", 0)

    # ── Utterances ───────────────────────────────────────────────

    def get_utterances(self, episode_id: int) -> list[dict]:
        r = self._get(f"/worker/episodes/{episode_id}/utterances")
        return r.json().get("utterances", [])

    def get_dirty_utterances_for_translate(self, episode_id: int) -> list[dict]:
        r = self._get(f"/worker/episodes/{episode_id}/utterances", params={"dirty": "translate"})
        return r.json().get("utterances", [])

    def get_dirty_utterances_for_tts(self, episode_id: int) -> list[dict]:
        r = self._get(f"/worker/episodes/{episode_id}/utterances", params={"dirty": "tts"})
        return r.json().get("utterances", [])

    def update_utterance(self, utterance_id: int, **fields) -> None:
        self._patch(f"/worker/utterances/{utterance_id}", json=fields)

    def calculate_utterances(
        self, episode_id: int, *, max_gap_ms: int = 500, max_duration_ms: int = 10000,
    ) -> list[dict]:
        r = self._post(f"/worker/episodes/{episode_id}/utterances/calculate", json={
            "max_gap_ms": max_gap_ms,
            "max_duration_ms": max_duration_ms,
        })
        return r.json().get("utterances", [])

    # ── Roles (by drama_id) ──────────────────────────────────────

    def get_roles_by_id(self, drama_id: int) -> dict[int, str]:
        r = self._get(f"/worker/dramas/{drama_id}/roles")
        data = r.json().get("by_id", {})
        return {int(k): v for k, v in data.items()}

    def get_role_name_map(self, drama_id: int) -> dict[int, str]:
        r = self._get(f"/worker/dramas/{drama_id}/roles")
        data = r.json().get("name_map", {})
        return {int(k): v for k, v in data.items()}

    # ── Glossary (by drama_id) ───────────────────────────────────

    def get_dict_map(self, drama_id: int, type: str) -> dict[str, str]:
        r = self._get(f"/worker/dramas/{drama_id}/glossary", params={"type": type})
        return r.json().get("map", {})

    def upsert_dict_entry(self, drama_id: int, type: str, src: str, target: str) -> None:
        self._post(f"/worker/dramas/{drama_id}/glossary", json={
            "type": type, "src": src, "target": target,
        })

    # ── Artifacts ────────────────────────────────────────────────

    def upsert_artifact(
        self, episode_id: int, kind: str, *,
        gcs_path: Optional[str] = None, checksum: Optional[str] = None,
    ) -> None:
        self._post(f"/worker/episodes/{episode_id}/artifacts", json={
            "kind": kind, "gcs_path": gcs_path, "checksum": checksum,
        })

    # ── Task context (for DbManifest) ────────────────────────────

    def get_latest_succeeded_task(self, episode_id: int, task_type: str) -> Optional[dict]:
        r = self._get("/worker/tasks/latest-succeeded", params={
            "episode_id": episode_id, "type": task_type,
        })
        return r.json().get("task")

    def update_task_context(self, task_id: int, updates: dict) -> None:
        self._patch(f"/worker/tasks/{task_id}/context", json=updates)
