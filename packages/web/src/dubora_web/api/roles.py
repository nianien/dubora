"""
Roles API: DB-backed per-drama 角色映射（array format with id）
"""
from typing import List, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from dubora_core.store import DbStore
from dubora_web.api._helpers import get_user_id, require_drama_owner

router = APIRouter()


def _get_store(db_path) -> DbStore:
    return DbStore(db_path)


@router.get("/episodes/{drama}/roles")
async def get_roles(drama: str, request: Request) -> dict:
    """读取角色映射（从 DB），返回 {roles: [{id, name, voice_type}]}"""
    store = _get_store(request.app.state.db_path)
    try:
        drama_row = store.get_drama_by_name(drama)
        if not drama_row:
            return {"roles": []}
        require_drama_owner(store, drama_row["id"], get_user_id(request))
        roles = store.get_roles(drama_row["id"])
        return {"roles": [{"id": r["id"], "name": r["name"], "voice_type": r["voice_type"], "role_type": r.get("role_type", "extra")} for r in roles]}
    finally:
        store.close()


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
    store = _get_store(request.app.state.db_path)
    try:
        user_id = get_user_id(request)
        drama_id = store.ensure_drama(name=drama, user_id=user_id)
        require_drama_owner(store, drama_id, user_id)
        role_dicts = [r.model_dump() for r in body.roles]
        updated = store.set_roles_by_list(drama_id, role_dicts)
        return {"roles": [{"id": r["id"], "name": r["name"], "voice_type": r["voice_type"], "role_type": r.get("role_type", "extra")} for r in updated]}
    finally:
        store.close()
