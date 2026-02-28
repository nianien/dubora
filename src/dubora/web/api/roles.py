"""
Roles API: 读写 roles.json（per-drama 角色映射）
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dubora.pipeline.processors.voiceprint.speaker_to_role import (
    _load_roles,
    _save_roles,
)

router = APIRouter()


def _roles_path(videos_dir: Path, drama: str) -> Path:
    """roles.json 路径: {videos_dir}/{drama}/dub/dict/roles.json"""
    return videos_dir / drama / "dub" / "dict" / "roles.json"


@router.get("/episodes/{drama}/roles")
async def get_roles(drama: str, request: Request) -> dict:
    """读取 roles.json"""
    videos_dir: Path = request.app.state.videos_dir
    path = _roles_path(videos_dir, drama)
    data = _load_roles(str(path))
    return data


class RolesBody(BaseModel):
    roles: dict = {}
    default_roles: dict = {}


@router.put("/episodes/{drama}/roles")
async def put_roles(drama: str, body: RolesBody, request: Request) -> dict:
    """保存 roles.json"""
    videos_dir: Path = request.app.state.videos_dir
    path = _roles_path(videos_dir, drama)
    data = {
        "roles": body.roles,
        "default_roles": body.default_roles,
    }
    _save_roles(data, str(path))
    return data
