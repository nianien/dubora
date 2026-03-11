"""
Worker API: HTTP endpoints for pipeline worker to access DB remotely.

All endpoints are under /worker/ prefix (mounted at /api by server.py).
Worker calls these instead of direct SQLite access when running on a separate machine.
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from dubora_core.events import EventEmitter, PipelineEvent
from dubora_core.phase_registry import PHASE_NAMES, GATE_AFTER
from dubora_core.store import DbStore
from dubora_core.submit import PipelineReactor, submit_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_store(db_path: Path) -> DbStore:
    return DbStore(db_path)


# ── Task lifecycle ───────────────────────────────────────────────


@router.post("/worker/submit")
async def worker_submit(request: Request) -> dict:
    """Submit a pipeline (replaces vsd-pipeline run direct DB access)."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)

    drama_name = body["drama"]
    episode_numbers = body["episodes"]  # list of int
    from_phase = body.get("from_phase")
    to_phase = body.get("to_phase")

    results = []
    for ep_num in episode_numbers:
        ep = store.get_episode_by_names(drama_name, int(ep_num))
        if ep is None:
            results.append({"episode": ep_num, "status": "not_found"})
            continue
        try:
            submit_pipeline(
                store, ep["id"], PHASE_NAMES, GATE_AFTER,
                from_phase=from_phase, to_phase=to_phase,
            )
            results.append({"episode": ep_num, "status": "submitted", "episode_id": ep["id"]})
        except Exception as e:
            results.append({"episode": ep_num, "status": "error", "error": str(e)})

    return {"results": results}


@router.post("/worker/claim")
async def worker_claim(request: Request) -> dict:
    """Claim next pending task (atomic: pending -> running)."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)

    executable_types = body.get("executable_types", PHASE_NAMES)
    task = store.claim_any_pending_task(executable_types=executable_types)

    return {"task": task}


@router.post("/worker/tasks/{task_id}/complete")
async def worker_complete_task(task_id: int, request: Request) -> dict:
    """Mark task as succeeded, run reactor to create next task."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)

    store.complete_task(task_id)

    # Run reactor on web side
    episode_id = body["episode_id"]
    task_type = body["task_type"]
    from_phase = body.get("from_phase")
    to_phase = body.get("to_phase")

    emitter = EventEmitter()
    reactor = PipelineReactor(
        store, emitter, episode_id, PHASE_NAMES, GATE_AFTER,
        from_phase=from_phase, to_phase=to_phase,
    )
    reactor(PipelineEvent(
        kind="task_succeeded",
        run_id=str(episode_id),
        data={"type": task_type},
    ))

    return {"status": "completed"}


@router.post("/worker/tasks/{task_id}/fail")
async def worker_fail_task(task_id: int, request: Request) -> dict:
    """Mark task as failed, run reactor to update episode status."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)

    error_msg = body.get("error")
    store.fail_task(task_id, error=error_msg)

    # Run reactor on web side
    episode_id = body["episode_id"]
    task_type = body["task_type"]

    emitter = EventEmitter()
    reactor = PipelineReactor(
        store, emitter, episode_id, PHASE_NAMES, GATE_AFTER,
        from_phase=body.get("from_phase"), to_phase=body.get("to_phase"),
    )
    reactor(PipelineEvent(
        kind="task_failed",
        run_id=str(episode_id),
        data={"type": task_type, "error": error_msg or ""},
    ))

    return {"status": "failed"}


# ── Data read ────────────────────────────────────────────────────


@router.get("/worker/episodes/{episode_id}")
async def worker_get_episode(episode_id: int, request: Request) -> dict:
    """Get episode + drama info."""
    store = _get_store(request.app.state.db_path)
    ep = store.get_episode(episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    synopsis = store.get_drama_synopsis(ep["drama_id"])
    return {"episode": ep, "synopsis": synopsis}


@router.get("/worker/episodes/{episode_id}/cues")
async def worker_get_cues(
    episode_id: int,
    request: Request,
    utterance_id: Optional[int] = Query(None),
) -> dict:
    """Get cues for an episode, optionally filtered by utterance_id."""
    store = _get_store(request.app.state.db_path)
    if utterance_id is not None:
        cues = store.get_cues_for_utterance(utterance_id)
    else:
        cues = store.get_cues(episode_id)
    return {"cues": cues}


@router.get("/worker/episodes/{episode_id}/utterances")
async def worker_get_utterances(
    episode_id: int,
    request: Request,
    dirty: Optional[str] = Query(None),
) -> dict:
    """Get utterances, optionally only dirty ones for translate or tts."""
    store = _get_store(request.app.state.db_path)
    if dirty == "translate":
        utts = store.get_dirty_utterances_for_translate(episode_id)
    elif dirty == "tts":
        utts = store.get_dirty_utterances_for_tts(episode_id)
    else:
        utts = store.get_utterances(episode_id)
    return {"utterances": utts}


@router.get("/worker/episodes/{episode_id}/roles")
async def worker_get_roles(episode_id: int, request: Request) -> dict:
    """Get roles for the episode's drama (by_id + name_map)."""
    store = _get_store(request.app.state.db_path)
    ep = store.get_episode(episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    drama_id = ep["drama_id"]
    return {
        "by_id": {str(k): v for k, v in store.get_roles_by_id(drama_id).items()},
        "name_map": {str(k): v for k, v in store.get_role_name_map(drama_id).items()},
    }


@router.get("/worker/episodes/{episode_id}/glossary")
async def worker_get_glossary(
    episode_id: int,
    request: Request,
    type: Optional[str] = Query(None),
) -> dict:
    """Get glossary entries or dict map for the episode's drama."""
    store = _get_store(request.app.state.db_path)
    ep = store.get_episode(episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    drama_id = ep["drama_id"]
    if type:
        return {"map": store.get_dict_map(drama_id, type)}
    return {"entries": store.get_dict_entries(drama_id)}


@router.get("/worker/tasks/latest-succeeded")
async def worker_latest_succeeded_task(
    request: Request,
    episode_id: int = Query(...),
    type: str = Query(...),
) -> dict:
    """Get the latest succeeded task of a given type."""
    store = _get_store(request.app.state.db_path)
    task = store.get_latest_succeeded_task(episode_id, type)
    return {"task": task}


@router.get("/worker/episodes/{episode_id}/has-cues")
async def worker_has_cues(episode_id: int, request: Request) -> dict:
    """Check if episode has any cues."""
    store = _get_store(request.app.state.db_path)
    return {"has_cues": store.has_cues(episode_id)}


# ── Data write ───────────────────────────────────────────────────


@router.post("/worker/episodes/{episode_id}/cues/insert")
async def worker_insert_cues(episode_id: int, request: Request) -> dict:
    """Batch insert cues."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)
    cue_ids = store.insert_cues(episode_id, body["cues"])
    return {"cue_ids": cue_ids}


@router.delete("/worker/episodes/{episode_id}/cues")
async def worker_delete_episode_cues(episode_id: int, request: Request) -> dict:
    """Delete all cues for an episode."""
    store = _get_store(request.app.state.db_path)
    count = store.delete_episode_cues(episode_id)
    return {"deleted": count}


@router.delete("/worker/episodes/{episode_id}/utterances")
async def worker_delete_episode_utterances(episode_id: int, request: Request) -> dict:
    """Delete all utterances for an episode."""
    store = _get_store(request.app.state.db_path)
    count = store.delete_episode_utterances(episode_id)
    return {"deleted": count}


@router.patch("/worker/cues/{cue_id}")
async def worker_update_cue(cue_id: int, request: Request) -> dict:
    """Update a single cue's fields."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)
    store.update_cue(cue_id, **body)
    return {"status": "updated"}


@router.patch("/worker/utterances/{utterance_id}")
async def worker_update_utterance(utterance_id: int, request: Request) -> dict:
    """Update a single utterance's fields."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)
    store.update_utterance(utterance_id, **body)
    return {"status": "updated"}


@router.post("/worker/episodes/{episode_id}/utterances/calculate")
async def worker_calculate_utterances(episode_id: int, request: Request) -> dict:
    """Recalculate utterance grouping from cues."""
    body = await request.json() if await request.body() else {}
    store = _get_store(request.app.state.db_path)
    max_gap_ms = body.get("max_gap_ms", 500)
    max_duration_ms = body.get("max_duration_ms", 10000)
    utts = store.calculate_utterances(
        episode_id, max_gap_ms=max_gap_ms, max_duration_ms=max_duration_ms,
    )
    return {"utterances": utts}


@router.post("/worker/episodes/{episode_id}/glossary")
async def worker_upsert_glossary(episode_id: int, request: Request) -> dict:
    """Upsert a glossary entry for the episode's drama."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)
    ep = store.get_episode(episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    drama_id = ep["drama_id"]
    store.upsert_dict_entry(drama_id, body["type"], body["src"], body["target"])
    return {"status": "upserted"}


@router.post("/worker/episodes/{episode_id}/artifacts")
async def worker_upsert_artifact(episode_id: int, request: Request) -> dict:
    """Upsert an artifact record."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)
    store.upsert_artifact(
        episode_id, body["kind"],
        gcs_path=body.get("gcs_path"),
        checksum=body.get("checksum"),
    )
    return {"status": "upserted"}


@router.patch("/worker/tasks/{task_id}/context")
async def worker_update_task_context(task_id: int, request: Request) -> dict:
    """Merge updates into task context JSON."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)
    store.update_task_context(task_id, body)
    return {"status": "updated"}


@router.get("/worker/episodes/{episode_id}/drama-synopsis")
async def worker_get_drama_synopsis(episode_id: int, request: Request) -> dict:
    """Get drama synopsis for an episode."""
    store = _get_store(request.app.state.db_path)
    ep = store.get_episode(episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"synopsis": store.get_drama_synopsis(ep["drama_id"])}


# ── Drama-level data (keyed by drama_id) ────────────────────────
# These mirror DbStore methods that take drama_id directly,
# used by DictLoader, TTS phase, etc.


@router.get("/worker/dramas/{drama_id}/synopsis")
async def worker_get_synopsis_by_drama(drama_id: int, request: Request) -> dict:
    """Get drama synopsis by drama_id."""
    store = _get_store(request.app.state.db_path)
    return {"synopsis": store.get_drama_synopsis(drama_id)}


@router.get("/worker/dramas/{drama_id}/glossary")
async def worker_get_glossary_by_drama(
    drama_id: int,
    request: Request,
    type: Optional[str] = Query(None),
) -> dict:
    """Get glossary dict map or entries for a drama."""
    store = _get_store(request.app.state.db_path)
    if type:
        return {"map": store.get_dict_map(drama_id, type)}
    return {"entries": store.get_dict_entries(drama_id)}


@router.post("/worker/dramas/{drama_id}/glossary")
async def worker_upsert_glossary_by_drama(drama_id: int, request: Request) -> dict:
    """Upsert a glossary entry by drama_id."""
    body = await request.json()
    store = _get_store(request.app.state.db_path)
    store.upsert_dict_entry(drama_id, body["type"], body["src"], body["target"])
    return {"status": "upserted"}


@router.get("/worker/dramas/{drama_id}/roles")
async def worker_get_roles_by_drama(drama_id: int, request: Request) -> dict:
    """Get roles by_id + name_map for a drama."""
    store = _get_store(request.app.state.db_path)
    return {
        "by_id": {str(k): v for k, v in store.get_roles_by_id(drama_id).items()},
        "name_map": {str(k): v for k, v in store.get_role_name_map(drama_id).items()},
    }
