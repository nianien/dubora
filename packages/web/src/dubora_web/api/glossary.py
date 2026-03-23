"""
Glossary API: query and manage translation glossary terms
"""
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dubora_web.api._helpers import get_user_id, require_drama_owner

router = APIRouter()


@router.get("/glossary")
async def list_glossary(request: Request, drama_id: Optional[int] = None) -> List[dict]:
    """Return all glossary entries, optionally filtered by drama_id."""
    store = request.app.state.store
    user_id = get_user_id(request)
    if drama_id is not None:
        require_drama_owner(store, drama_id, user_id)
        rows = store._execute(
            """SELECT g.id, g.drama_id, dm.name AS drama_name, g.type, g.src, g.target
               FROM glossary g
               LEFT JOIN dramas dm ON g.drama_id = dm.id
               WHERE g.drama_id = %s
               ORDER BY g.type, g.src""",
            (drama_id,),
        ).fetchall()
    elif user_id is not None:
        rows = store._execute(
            """SELECT g.id, g.drama_id, dm.name AS drama_name, g.type, g.src, g.target
               FROM glossary g
               LEFT JOIN dramas dm ON g.drama_id = dm.id
               WHERE dm.user_id = %s
               ORDER BY g.drama_id, g.type, g.src""",
            (user_id,),
        ).fetchall()
    else:
        rows = store._execute(
            """SELECT g.id, g.drama_id, dm.name AS drama_name, g.type, g.src, g.target
               FROM glossary g
               LEFT JOIN dramas dm ON g.drama_id = dm.id
               ORDER BY g.drama_id, g.type, g.src""",
        ).fetchall()
    return [dict(r) for r in rows]


class GlossaryEntryBody(BaseModel):
    drama_id: int
    type: str
    src: str
    target: str


@router.post("/glossary")
async def create_entry(request: Request, body: GlossaryEntryBody) -> dict:
    """Create a new glossary entry."""
    if not body.drama_id:
        raise HTTPException(status_code=400, detail="drama_id is required")
    store = request.app.state.store
    require_drama_owner(store, body.drama_id, get_user_id(request))
    cursor = store._execute(
        """INSERT INTO glossary (drama_id, type, src, target) VALUES (%s, %s, %s, %s)
           ON CONFLICT(drama_id, type, src) DO UPDATE SET target=excluded.target
           RETURNING id""",
        (body.drama_id, body.type, body.src, body.target),
    )
    row_id = cursor.fetchone()["id"]
    store._conn.commit()
    return {"id": row_id, **body.model_dump()}


@router.put("/glossary/{entry_id}")
async def update_entry(request: Request, entry_id: int, body: GlossaryEntryBody) -> dict:
    """Update an existing glossary entry."""
    store = request.app.state.store
    user_id = get_user_id(request)
    # Verify the entry exists and belongs to current user's drama
    existing = store._execute("SELECT drama_id FROM glossary WHERE id=%s", (entry_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Glossary entry not found")
    require_drama_owner(store, existing["drama_id"], user_id)
    # Also verify target drama ownership if drama_id changed
    if body.drama_id != existing["drama_id"]:
        require_drama_owner(store, body.drama_id, user_id)
    store._execute(
        "UPDATE glossary SET drama_id=%s, type=%s, src=%s, target=%s WHERE id=%s",
        (body.drama_id, body.type, body.src, body.target, entry_id),
    )
    store._conn.commit()
    return {"id": entry_id, **body.model_dump()}


@router.delete("/glossary/{entry_id}")
async def delete_entry(request: Request, entry_id: int) -> dict:
    """Delete a glossary entry."""
    store = request.app.state.store
    row = store._execute("SELECT drama_id FROM glossary WHERE id=%s", (entry_id,)).fetchone()
    if row:
        require_drama_owner(store, row["drama_id"], get_user_id(request))
    store._execute("DELETE FROM glossary WHERE id=%s", (entry_id,))
    store._conn.commit()
    return {"deleted": entry_id}
