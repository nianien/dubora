"""
Glossary API: query and manage translation glossary terms
"""
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from dubora_core.store import DbStore

router = APIRouter()


def _get_store(db_path: Path) -> DbStore:
    return DbStore(db_path)


@router.get("/glossary")
async def list_glossary(request: Request, drama_id: Optional[int] = None) -> List[dict]:
    """Return all glossary entries, optionally filtered by drama_id."""
    store = _get_store(request.app.state.db_path)
    if drama_id is not None:
        rows = store._conn.execute(
            """SELECT g.id, g.drama_id, dm.name AS drama_name, g.type, g.src, g.target
               FROM glossary g
               LEFT JOIN dramas dm ON g.drama_id = dm.id
               WHERE g.drama_id = ?
               ORDER BY g.type, g.src""",
            (drama_id,),
        ).fetchall()
    else:
        rows = store._conn.execute(
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
    store = _get_store(request.app.state.db_path)
    cursor = store._conn.execute(
        "INSERT INTO glossary (drama_id, type, src, target) VALUES (?, ?, ?, ?)",
        (body.drama_id, body.type, body.src, body.target),
    )
    store._conn.commit()
    return {"id": cursor.lastrowid, **body.model_dump()}


@router.put("/glossary/{entry_id}")
async def update_entry(request: Request, entry_id: int, body: GlossaryEntryBody) -> dict:
    """Update an existing glossary entry."""
    store = _get_store(request.app.state.db_path)
    store._conn.execute(
        "UPDATE glossary SET drama_id=?, type=?, src=?, target=? WHERE id=?",
        (body.drama_id, body.type, body.src, body.target, entry_id),
    )
    store._conn.commit()
    return {"id": entry_id, **body.model_dump()}


@router.delete("/glossary/{entry_id}")
async def delete_entry(request: Request, entry_id: int) -> dict:
    """Delete a glossary entry."""
    store = _get_store(request.app.state.db_path)
    store._conn.execute("DELETE FROM glossary WHERE id=?", (entry_id,))
    store._conn.commit()
    return {"deleted": entry_id}
