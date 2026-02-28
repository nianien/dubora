"""
Emotions API: 全局 emotions 配置
"""
from typing import Any, Dict, List

from fastapi import APIRouter

from dubora.config import load_emotions

router = APIRouter()


@router.get("/emotions")
async def get_emotions() -> List[Dict[str, Any]]:
    """返回全局 emotions 列表。[{"name": "...", "value": "...", "lang": [...]}, ...]"""
    return load_emotions()
