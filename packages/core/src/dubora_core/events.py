"""
Pipeline event system: structured events for engine + SSE push.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from dubora_core.utils.logger import info, warning, error as log_error


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PipelineEvent:
    """A single pipeline event."""
    kind: str           # "extract_done", "gate_awaiting", "pipeline_done", ...
    run_id: str         # identifies this run
    ts: str = ""        # ISO 8601 UTC
    phase: str = ""     # related phase name (optional)
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.ts:
            self.ts = _now_iso()


class EventEmitter:
    """In-memory event bus. Events do NOT go to DB — they drive the reactor and listeners."""

    def __init__(self):
        self._listeners: list[Callable[[PipelineEvent], None]] = []

    def on(self, listener: Callable[[PipelineEvent], None]) -> None:
        self._listeners.append(listener)

    def emit(self, event: PipelineEvent) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Listeners must not break the pipeline


class LogListener:
    """Listener that logs PipelineEvents to the console logger."""

    _LOG_MAP = {
        "pipeline_start": ("Pipeline started", info),
        "pipeline_done": ("Pipeline completed", info),
        "pipeline_failed": ("Pipeline failed", log_error),
        "pipeline_stopped": ("Pipeline stopped", warning),
        "gate_awaiting": ("Pipeline paused at gate", info),
    }

    def __call__(self, event: PipelineEvent) -> None:
        kind = event.kind
        data = event.data

        if kind in self._LOG_MAP:
            msg, log_fn = self._LOG_MAP[kind]
            detail = data.get("message", "")
            log_fn(f"{msg}: {detail}" if detail else msg)
        elif kind.endswith("_start"):
            phase = kind.removesuffix("_start")
            info(f"\n{'=' * 60}")
            info(f"Phase: {phase}")
            info(f"{'=' * 60}")
        elif kind.endswith("_done"):
            phase = kind.removesuffix("_done")
            info(f"Phase '{phase}' succeeded")
        elif kind.endswith("_skipped"):
            phase = kind.removesuffix("_skipped")
            reason = data.get("reason", "")
            info(f"Phase '{phase}' skipped: {reason}")
        elif kind.endswith("_failed"):
            phase = kind.removesuffix("_failed")
            msg = data.get("error", "unknown error")
            log_error(f"Phase '{phase}' failed: {msg}")
