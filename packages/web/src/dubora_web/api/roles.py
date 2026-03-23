"""
Roles API: DB-backed per-drama 角色映射（array format with id）
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dubora_core.store import DbStore
from dubora_web.api._helpers import get_user_id

router = APIRouter()


def _resolve_drama(store: DbStore, drama: str, user_id: int | None) -> dict:
    """Lookup drama by name. Raises 404 if not found."""
    row = store.get_drama_by_name(drama, user_id=user_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Drama not found: {drama}")
    return row


@router.get("/episodes/{drama}/roles")
async def get_roles(drama: str, request: Request) -> dict:
    """读取角色映射（从 DB），返回 {roles: [{id, name, voice_type}]}"""
    store = request.app.state.store
    user_id = get_user_id(request)
    drama_row = store.get_drama_by_name(drama, user_id=user_id)
    if not drama_row:
        return {"roles": []}
    roles = store.get_roles(drama_row["id"])
    return {"roles": [{"id": r["id"], "name": r["name"], "voice_type": r["voice_type"], "role_type": r.get("role_type", "extra")} for r in roles]}


class RoleItem(BaseModel):
    id: Optional[int] = None
    name: str
    voice_type: str = ""
    role_type: str = "extra"


class RolesBody(BaseModel):
    roles: List[RoleItem] = []


@router.put("/episodes/{drama}/roles")
async def put_roles(drama: str, body: RolesBody, request: Request) -> dict:
    """保存角色映射（到 DB），有 id 更新，无 id 新建"""
    store = request.app.state.store
    user_id = get_user_id(request)
    drama_row = _resolve_drama(store, drama, user_id)
    role_dicts = [r.model_dump() for r in body.roles]
    updated = store.set_roles_by_list(drama_row["id"], role_dicts)
    return {"roles": [{"id": r["id"], "name": r["name"], "voice_type": r["voice_type"], "role_type": r.get("role_type", "extra")} for r in updated]}
