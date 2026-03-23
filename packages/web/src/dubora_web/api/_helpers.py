"""Permission helpers for API routers."""
from fastapi import HTTPException

from dubora_core.store import DbStore


def get_user_id(request) -> int | None:
    """Get current user ID. Returns None when auth is disabled."""
    return getattr(request.state, "user_id", None)


def require_drama_owner(store: DbStore, drama_id: int, user_id: int | None) -> None:
    """Verify drama belongs to current user. Raises 403 if not.

    Skipped when user_id is None (auth disabled).
    """
    if user_id is None:
        return
    row = store._execute(
        "SELECT user_id FROM dramas WHERE id=%s", (drama_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Drama not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="No permission to access this drama")


def require_episode_owner(store: DbStore, episode_id: int, user_id: int | None) -> None:
    """Verify episode's drama belongs to current user via episode -> drama chain.

    Skipped when user_id is None (auth disabled).
    """
    if user_id is None:
        return
    row = store._execute(
        """SELECT d.user_id FROM episodes e
           JOIN dramas d ON e.drama_id = d.id
           WHERE e.id=%s""",
        (episode_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="No permission to access this episode")
