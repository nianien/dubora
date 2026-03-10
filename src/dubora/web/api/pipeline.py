"""
Pipeline API: submit tasks + SSE status polling.

Submit endpoints write to DB. Worker thread (in ide) or standalone worker executes.
SSE endpoint only polls task status from DB.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from dubora.config.settings import PipelineConfig
from dubora.pipeline.phases import ALL_PHASES, GATE_AFTER, GATES, STAGES, build_phases
from dubora.pipeline.core.store import PipelineStore
from dubora.pipeline.core.events import EventEmitter
from dubora.pipeline.core.worker import PipelineReactor, submit_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()

PHASES_META = [{"name": p.name, "label": p.label} for p in ALL_PHASES]


def _get_store(db_path: Path) -> PipelineStore:
    return PipelineStore(db_path)


def _derive_phase_status(store: PipelineStore, episode_id: int) -> list[dict]:
    tasks = store.get_tasks(episode_id)
    task_map: dict[str, dict] = {}
    for t in tasks:
        task_map[t["type"]] = t

    result = []
    for meta in PHASES_META:
        t = task_map.get(meta["name"])
        if t:
            result.append({
                "name": meta["name"],
                "label": meta["label"],
                "status": t["status"],
                "started_at": t.get("claimed_at"),
                "finished_at": t.get("finished_at"),
                "error": t.get("error"),
            })
        else:
            result.append({
                "name": meta["name"],
                "label": meta["label"],
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "error": None,
            })
    return result


def _derive_gate_status(store: PipelineStore, episode_id: int) -> list[dict]:
    tasks = store.get_tasks(episode_id)
    task_map: dict[str, dict] = {}
    for t in tasks:
        task_map[t["type"]] = t

    result = []
    for gate_def in GATES:
        gate_task = task_map.get(gate_def["key"])
        before_phase = task_map.get(gate_def["after"])

        if gate_task and gate_task["status"] == "succeeded":
            status = "passed"
        elif before_phase and before_phase["status"] == "succeeded":
            # phase 已完成但 gate 未通过 → 等待人工审核
            status = "awaiting"
        else:
            status = "pending"

        result.append({
            "key": gate_def["key"],
            "after": gate_def["after"],
            "label": gate_def["label"],
            "status": status,
        })
    return result


def _derive_stages(phases: list[dict]) -> list[dict]:
    phase_map = {p["name"]: p for p in phases}
    result = []
    for stage_def in STAGES:
        child_phases = [phase_map.get(pn) for pn in stage_def["phases"] if phase_map.get(pn)]
        if any(p["status"] == "failed" for p in child_phases):
            status = "failed"
        elif any(p["status"] == "running" for p in child_phases):
            status = "running"
        elif all(p["status"] in ("succeeded", "skipped") for p in child_phases):
            status = "succeeded"
        else:
            status = "pending"
        result.append({
            "key": stage_def["key"],
            "label": stage_def["label"],
            "phases": stage_def["phases"],
            "status": status,
        })
    return result


# --------------------------------------------------------------------------- #
# GET /episodes/{drama}/{ep}/pipeline/status
# --------------------------------------------------------------------------- #

@router.get("/episodes/{drama}/{ep}/pipeline/status")
async def pipeline_status(request: Request, drama: str, ep: str) -> dict:
    store = _get_store(request.app.state.db_path)

    episode = store.get_episode_by_names(drama, int(ep))

    if episode is None:
        phases = [{
            "name": m["name"], "label": m["label"], "status": "pending",
            "started_at": None, "finished_at": None, "error": None,
        } for m in PHASES_META]
        gates = [{"key": g["key"], "after": g["after"], "label": g["label"], "status": "pending"} for g in GATES]
        return {
            "has_manifest": False,
            "phases": phases,
            "gates": gates,
            "stages": _derive_stages(phases),
        }

    episode_id = episode["id"]
    phases = _derive_phase_status(store, episode_id)
    return {
        "has_manifest": True,
        "episode_status": episode["status"],
        "phases": phases,
        "gates": _derive_gate_status(store, episode_id),
        "stages": _derive_stages(phases),
    }


# --------------------------------------------------------------------------- #
# POST /episodes/{drama}/{ep}/pipeline/run  (submit only)
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/run")
async def run_pipeline(request: Request, drama: str, ep: str) -> dict:
    """Submit pipeline tasks to DB. Worker thread executes them."""
    store = _get_store(request.app.state.db_path)

    episode = store.get_episode_by_names(drama, int(ep))
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {drama}/{ep}")

    if store.has_running_tasks(episode["id"]):
        raise HTTPException(status_code=409, detail="Pipeline has running tasks")

    body = await request.json() if await request.body() else {}
    from_phase = body.get("from_phase")
    to_phase = body.get("to_phase")

    phases = build_phases(PipelineConfig())
    submit_pipeline(store, episode["id"], phases, GATE_AFTER,
                    from_phase=from_phase, to_phase=to_phase)

    return {"status": "submitted", "episode_id": episode["id"]}


# --------------------------------------------------------------------------- #
# GET /episodes/{drama}/{ep}/pipeline/stream  (SSE, poll only)
# --------------------------------------------------------------------------- #

@router.get("/episodes/{drama}/{ep}/pipeline/stream")
async def pipeline_stream(request: Request, drama: str, ep: str):
    """SSE: poll task status changes from DB. Does NOT execute tasks."""

    async def event_generator():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        store = _get_store(request.app.state.db_path)
        episode = store.get_episode_by_names(drama, int(ep))
        if episode is None:
            yield sse("error", {"message": f"Episode not found: {drama}/{ep}"})
            return

        episode_id = episode["id"]
        seen: set[tuple[int, str]] = set()

        while True:
            if await request.is_disconnected():
                break

            tasks = store.get_tasks(episode_id)
            for t in tasks:
                key = (t["id"], t["status"])
                if key not in seen and t["status"] != "pending":
                    seen.add(key)
                    yield sse(
                        f"{t['type']}_{t['status']}",
                        {"type": t["type"], "task_id": t["id"]},
                    )

            ep_row = store.get_episode(episode_id)
            ep_status = ep_row["status"] if ep_row else "unknown"
            if ep_status in ("succeeded", "failed"):
                data = {}
                if ep_status == "failed":
                    latest = store.get_latest_task(episode_id)
                    if latest and latest.get("error"):
                        data["error"] = f"Phase '{latest['type']}' failed: {latest['error']}"
                    else:
                        data["error"] = "Pipeline failed (unknown error)"
                yield sse(f"pipeline_{ep_status}", data)
                break
            if ep_status == "ready":
                # Stopped — no more tasks to watch
                yield sse("pipeline_stopped", {})
                break
            if ep_status == "review":
                # Check if there are active phase tasks (not gate tasks) — if so, keep polling
                gate_keys = {g["key"] for g in GATES}
                has_active_phase = any(
                    t["status"] in ("pending", "running") and t["type"] not in gate_keys
                    for t in tasks
                )
                if has_active_phase:
                    await asyncio.sleep(0.3)
                    continue
                gate_tasks = [t for t in tasks if t["type"] in gate_keys
                              and t["status"] == "pending"]
                gate_key = gate_tasks[0]["type"] if gate_tasks else ""
                yield sse("gate_awaiting", {"gate": gate_key})
                break

            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# POST /episodes/{drama}/{ep}/pipeline/cancel
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/cancel")
async def cancel_pipeline(request: Request, drama: str, ep: str) -> dict:
    store = _get_store(request.app.state.db_path)

    episode = store.get_episode_by_names(drama, int(ep))
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {drama}/{ep}")

    store.delete_pending_tasks(episode["id"])
    status = store.derive_episode_status(episode["id"])
    store.update_episode_status(episode["id"], status)

    return {"status": "stopped", "episode_status": status}


# --------------------------------------------------------------------------- #
# POST /episodes/{drama}/{ep}/pipeline/gate/{gate_key}/pass
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/gate/{gate_key}/pass")
async def pass_gate(request: Request, drama: str, ep: str, gate_key: str) -> dict:
    store = _get_store(request.app.state.db_path)

    episode = store.get_episode_by_names(drama, int(ep))
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {drama}/{ep}")
    episode_id = episode["id"]

    # Idempotency: if already has pending/running phase task, skip
    latest = store.get_latest_task(episode_id)
    if latest and latest["status"] in ("pending", "running") and latest["type"] != gate_key:
        return {"status": "passed", "gate": gate_key}

    task_id = store.pass_gate_task(episode_id, gate_key)
    if task_id is None:
        # No pending gate task — create and pass, or already passed
        gate_task = store.get_gate_task(episode_id, gate_key)
        if gate_task and gate_task["status"] == "succeeded":
            return {"status": "passed", "gate": gate_key}
        task_id = store.create_task(episode_id, gate_key)
        store.complete_task(task_id)

    # Reactor creates the next phase task
    emitter = EventEmitter()
    phases = build_phases(PipelineConfig())
    reactor = PipelineReactor(
        store, emitter, episode_id, phases, GATE_AFTER,
    )
    from dubora.pipeline.core.events import PipelineEvent
    reactor._on_succeeded(PipelineEvent(
        kind="task_succeeded",
        run_id=str(episode_id),
        data={"type": gate_key},
    ))

    return {"status": "passed", "gate": gate_key}


# --------------------------------------------------------------------------- #
# POST /episodes/{drama}/{ep}/pipeline/gate/{gate_key}/reset
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/gate/{gate_key}/reset")
async def reset_gate(request: Request, drama: str, ep: str, gate_key: str) -> dict:
    """Reset pipeline back to a gate. Deletes downstream tasks, creates pending gate task."""
    store = _get_store(request.app.state.db_path)

    episode = store.get_episode_by_names(drama, int(ep))
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {drama}/{ep}")

    store.reset_to_gate(episode["id"], gate_key)
    return {"status": "reset", "gate": gate_key}


# --------------------------------------------------------------------------- #
# GET /episodes/{drama}/{ep}/pipeline/events
# --------------------------------------------------------------------------- #

@router.get("/episodes/{drama}/{ep}/pipeline/events")
async def get_pipeline_events(request: Request, drama: str, ep: str) -> dict:
    store = _get_store(request.app.state.db_path)
    episode = store.get_episode_by_names(drama, int(ep))
    if episode is None:
        return {"events": []}
    events = store.get_events_for_episode(episode["id"])
    return {"events": events}
