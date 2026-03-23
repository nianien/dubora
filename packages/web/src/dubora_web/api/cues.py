"""
Cues API: GET/PUT cue-level data.
Also serves utterances endpoint.

DB cues are the single source of truth.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from dubora_core.store import DbStore
from dubora_web.api._helpers import get_user_id

router = APIRouter()


def _resolve_episode(store: DbStore, drama: str, ep: str, user_id: int | None = None) -> int:
    """Lookup episode by drama name + number. Raises 404 if not found."""
    episode = store.get_episode_by_names(drama, int(ep), user_id=user_id)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {drama}/{ep}")
    return episode["id"]


@router.get("/episodes/{drama}/{ep}/cues")
async def get_cues(request: Request, drama: str, ep: str) -> dict:
    """Get cues for an episode."""
    store = request.app.state.store
    episode_id = _resolve_episode(store, drama, ep, user_id=get_user_id(request))
    cues = store.get_cues(episode_id)
    return {"cues": cues}


@router.put("/episodes/{drama}/{ep}/cues")
async def put_cues(request: Request, drama: str, ep: str) -> dict:
    """Save cues with automatic dirty detection via diff_and_save.

    diff_and_save automatically calls calculate_utterances() at the end.
    """
    store = request.app.state.store
    episode_id = _resolve_episode(store, drama, ep, user_id=get_user_id(request))

    body = await request.json()
    incoming = body.get("cues", [])
    if not isinstance(incoming, list):
        raise HTTPException(status_code=400, detail="'cues' must be a list")

    updated = store.diff_and_save(episode_id, incoming)
    return {"cues": updated}


@router.get("/episodes/{drama}/{ep}/utterances")
async def get_utterances(request: Request, drama: str, ep: str) -> dict:
    """Get enriched utterances for an episode."""
    store = request.app.state.store
    episode_id = _resolve_episode(store, drama, ep, user_id=get_user_id(request))

    # Ensure utterances exist (lazy calculate if cues exist but utterances don't)
    utts = store.get_utterances(episode_id)
    if not utts:
        src_cues = store.get_cues(episode_id)
        if src_cues:
            utts = store.calculate_utterances(episode_id)

    return {"utterances": utts}
