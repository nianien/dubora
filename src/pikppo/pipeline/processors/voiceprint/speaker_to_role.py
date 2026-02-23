"""
声线分配（统一 role_cast.json）

数据结构（单文件 role_cast.json）：
{
  "speakers": { "pa": "PingAn", "el": "ErLv", ... },
  "roles":    { "PingAn": "en_male_hades_moon_bigtts", ... },
  "default_roles": { "male": "LrNan1", "female": "LrNv1", "unknown": "LrNan1" }
}

解析链路：
  已标注: speaker → role_id → voice_type
  未标注: speaker → default_roles[gender] → voice_type

提供两个核心函数：
- update_speakers(): Sub 阶段追加新 speaker
- resolve_voice_assignments(): TTS 阶段解析 speaker → voice_type
"""
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from pikppo.utils.logger import info


def _load_role_cast(file_path: str) -> Dict[str, Any]:
    """加载 role_cast.json，返回完整数据。不存在则返回空骨架。"""
    path = Path(file_path)
    if not path.exists():
        return {
            "speakers": {},
            "roles": {},
            "default_roles": {"male": "", "female": "", "unknown": ""},
        }

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 兼容旧版 roles 数组格式
    roles = data.get("roles", {})
    if isinstance(roles, list):
        data["roles"] = {e["role_id"]: e["voice_type"] for e in roles if e.get("role_id")}

    # 兼容旧版无 speakers 字段（纯 role_cast 文件）
    if "speakers" not in data:
        data["speakers"] = {}
    if "default_roles" not in data:
        data["default_roles"] = {}

    return data


def _save_role_cast(data: Dict[str, Any], file_path: str) -> None:
    """原子写入 role_cast.json。"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def update_speakers(
    speakers: List[str],
    file_path: str,
    episode_id: str = "",
) -> None:
    """
    追加新 speaker 到 role_cast.json（只添加，不覆盖已有赋值）。

    Sub 阶段完成后调用。

    Args:
        speakers: 本集发现的 speaker 列表
        file_path: role_cast.json 路径
        episode_id: 集编号（仅用于日志）
    """
    data = _load_role_cast(file_path)
    speaker_map = data["speakers"]

    added = []
    for spk in speakers:
        if spk not in speaker_map:
            speaker_map[spk] = ""
            added.append(spk)

    _save_role_cast(data, file_path)

    tag = f"[ep={episode_id}]" if episode_id else ""
    if added:
        info(f"role_cast{tag}: added {len(added)} new speakers: {added}")
    else:
        info(f"role_cast{tag}: no new speakers")


def resolve_voice_assignments(
    file_path: str,
    speaker_genders: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    从 role_cast.json 解析 speaker → voice_type 映射。

    Args:
        file_path: role_cast.json 路径
        speaker_genders: speaker → gender 映射（"male"/"female"/"unknown"）

    Returns:
        { "pa": {"voice_type": "en_male_...", "role_id": "PingAn"}, ... }
    """
    data = _load_role_cast(file_path)
    speaker_map = data.get("speakers", {})
    roles = data.get("roles", {})
    default_roles = data.get("default_roles", {})
    genders = speaker_genders or {}

    if not speaker_map:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for speaker, role_id in speaker_map.items():
        if not role_id:
            gender = genders.get(speaker, "unknown")
            role_id = default_roles.get(gender, default_roles.get("unknown", ""))

        if not role_id:
            info(f"role_cast: speaker '{speaker}' has no role assigned, skipping")
            continue

        voice_type = roles.get(role_id)
        if not voice_type:
            info(f"role_cast: role '{role_id}' not found in roles for speaker '{speaker}'")
            continue

        result[speaker] = {
            "voice_type": voice_type,
            "role_id": role_id,
        }

    return result
