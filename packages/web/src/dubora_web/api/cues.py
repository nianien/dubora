"""
Cues API: GET/PUT cue-level data.
Also serves utterances endpoint.

DB cues are the single source of truth.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from dubora_core.store import DbStore
from dubora_web.api._helpers import get_user_id, require_episode_owner

router = APIRouter()


def _get_store(db_path: Path) -> DbStore:
    return DbStore(db_path)


def _ensure_episode(store: DbStore, drama: str, ep: str, user_id: int | None = None) -> int:
    """Ensure drama + episode exist in DB, return episode_id."""
    drama_id = store.ensure_drama(name=drama, user_id=user_id)
    episode_id = store.ensure_episode(drama_id=drama_id, number=int(ep))
    return episode_id


@router.get("/episodes/{drama}/{ep}/cues")
async def get_cues(request: Request, drama: str, ep: str) -> dict:
    """Get cues for an episode."""
    store = _get_store(request.app.state.db_path)
    user_id = get_user_id(request)

    episode_id = _ensure_episode(store, drama, ep, user_id=user_id)
    require_episode_owner(store, episode_id, user_id)
    cues = store.get_cues(episode_id)

    return {"cues": cues}


@router.put("/episodes/{drama}/{ep}/cues")
async def put_cues(request: Request, drama: str, ep: str) -> dict:
    """Save cues with automatic dirty detection via diff_and_save.

    diff_and_save automatically calls calculate_utterances() at the end.
    """
    store = _get_store(request.app.state.db_path)
    user_id = get_user_id(request)

    episode_id = _ensure_episode(store, drama, ep, user_id=user_id)
    require_episode_owner(store, episode_id, user_id)

    body = await request.json()
    incoming = body.get("cues", [])
    if not isinstance(incoming, list):
        raise HTTPException(status_code=400, detail="'cues' must be a list")

    updated = store.diff_and_save(episode_id, incoming)

    return {"cues": updated}


@router.get("/episodes/{drama}/{ep}/utterances")
async def get_utterances(request: Request, drama: str, ep: str) -> dict:
    """Get enriched utterances for an episode."""
    store = _get_store(request.app.state.db_path)
    user_id = get_user_id(request)

    episode_id = _ensure_episode(store, drama, ep, user_id=user_id)
    require_episode_owner(store, episode_id, user_id)

    # Ensure utterances exist (lazy calculate if cues exist but utterances don't)
    utts = store.get_utterances(episode_id)
    if not utts:
        src_cues = store.get_cues(episode_id)
        if src_cues:
            utts = store.calculate_utterances(episode_id)

    return {"utterances": utts}
