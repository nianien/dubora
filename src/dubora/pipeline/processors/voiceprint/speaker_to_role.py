"""
声线分配（统一 roles.json）

数据结构（单文件 roles.json）：
{
  "roles":    { "PingAn": "en_male_hades_moon_bigtts", ... },
  "default_roles": { "male": "LrNan1", "female": "LrNv1", "unknown": "LrNan1" }
}

解析链路：
  已标注: role_id → voice_type
  未标注: default_roles[gender] → voice_type

核心函数：
- resolve_voice_assignments(): TTS 阶段解析 speaker → voice_type
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional

from dubora.utils.logger import info


def _load_roles(file_path: str) -> Dict[str, Any]:
    """加载 roles.json，返回完整数据。不存在则返回空骨架。"""
    path = Path(file_path)
    if not path.exists():
        return {
            "roles": {},
            "default_roles": {"male": "", "female": "", "unknown": ""},
        }

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 兼容旧版 roles 数组格式
    roles = data.get("roles", {})
    if isinstance(roles, list):
        data["roles"] = {e["role_id"]: e["voice_type"] for e in roles if e.get("role_id")}

    if "default_roles" not in data:
        data["default_roles"] = {}

    return data


def _save_roles(data: Dict[str, Any], file_path: str) -> None:
    """原子写入 roles.json。"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {"roles": data.get("roles", {}), "default_roles": data.get("default_roles", {})}
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def resolve_voice_assignments(
    file_path: str,
    speaker_genders: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    从 roles.json 解析 role_id → voice_type 映射。

    segment.speaker 现在直接存角色 ID，所以 role_id 就是 speaker。

    Args:
        file_path: roles.json 路径
        speaker_genders: role_id → gender 映射（"male"/"female"/"unknown"），用于 fallback

    Returns:
        { "PingAn": {"voice_type": "en_male_...", "role_id": "PingAn"}, ... }
    """
    data = _load_roles(file_path)
    roles = data.get("roles", {})
    default_roles = data.get("default_roles", {})
    genders = speaker_genders or {}

    if not roles:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for role_id, voice_type in roles.items():
        if not voice_type:
            # fallback to default by gender
            gender = genders.get(role_id, "unknown")
            fallback_role = default_roles.get(gender, default_roles.get("unknown", ""))
            if fallback_role and fallback_role in roles:
                voice_type = roles[fallback_role]

        if not voice_type:
            info(f"roles: role '{role_id}' has no voice_type assigned, skipping")
            continue

        result[role_id] = {
            "voice_type": voice_type,
            "role_id": role_id,
        }

    return result
