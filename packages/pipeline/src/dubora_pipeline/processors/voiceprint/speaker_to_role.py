"""
声线分配（DB-backed roles）

核心函数：
- resolve_voice_assignments(): TTS 阶段从 DB roles_map 解析 speaker → voice_type
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any, Optional

from dubora_core.utils.logger import info

if TYPE_CHECKING:
    from dubora_core.store import DbStore


def resolve_voice_assignments(
    store: DbStore,
    drama_id: int,
    speaker_genders: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    从 DB roles 表解析 role_id → voice_type 映射。

    Args:
        store: DbStore instance
        drama_id: drama ID
        speaker_genders: unused (kept for API compat), previously for default_roles fallback

    Returns:
        { "PingAn": {"voice_type": "en_male_...", "role_id": "PingAn"}, ... }
    """
    roles_map = store.get_roles_map(drama_id)

    result: Dict[str, Dict[str, Any]] = {}
    for role_id, voice_type in roles_map.items():
        if not voice_type:
            info(f"roles: role '{role_id}' has no voice_type assigned, skipping")
            continue
        result[role_id] = {
            "voice_type": voice_type,
            "role_id": role_id,
        }

    return result
