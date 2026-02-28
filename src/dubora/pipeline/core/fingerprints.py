"""
Fingerprint 计算：确定性 hash
"""
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from dubora.pipeline.core.types import Artifact


def _remove_none_and_empty(obj: Any) -> Any:
    """递归移除 None 值和空字典/列表。"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            v_cleaned = _remove_none_and_empty(v)
            if v_cleaned is not None and v_cleaned != {} and v_cleaned != []:
                result[k] = v_cleaned
        return result
    elif isinstance(obj, list):
        result = []
        for item in obj:
            item_cleaned = _remove_none_and_empty(item)
            if item_cleaned is not None and item_cleaned != {} and item_cleaned != []:
                result.append(item_cleaned)
        return result
    else:
        return obj


def canonicalize_json(obj: Any) -> str:
    """
    规范化 JSON（排序 key、去 null、稳定浮点格式）。
    
    Args:
        obj: 要规范化的对象
    
    Returns:
        规范化后的 JSON 字符串
    """
    # 先移除 None 和空值
    cleaned = _remove_none_and_empty(obj)
    return json.dumps(
        cleaned,
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':'),
        allow_nan=False,
    )


def hash_string(s: str) -> str:
    """计算字符串的 SHA256 hash。"""
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def hash_file(path: Path) -> str:
    """
    计算文件的 SHA256 hash。

    Args:
        path: 文件路径

    Returns:
        "sha256:..." 格式的 hash
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def hash_directory(path: Path) -> str:
    """
    计算目录的 SHA256 hash（基于所有文件的内容）。

    按文件名排序后依次 hash 每个文件的内容，确保确定性。

    Args:
        path: 目录路径

    Returns:
        "sha256:..." 格式的 hash
    """
    h = hashlib.sha256()

    # 获取目录下所有文件（递归），按相对路径排序
    all_files = sorted(path.rglob("*"))

    for file_path in all_files:
        if file_path.is_file():
            # 先 hash 相对路径（确保文件名变化会影响 hash）
            rel_path = str(file_path.relative_to(path))
            h.update(rel_path.encode("utf-8"))

            # 再 hash 文件内容
            with file_path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)

    return f"sha256:{h.hexdigest()}"


def hash_path(path: Path) -> str:
    """
    计算路径的 SHA256 hash（自动判断文件或目录）。

    Args:
        path: 文件或目录路径

    Returns:
        "sha256:..." 格式的 hash
    """
    if path.is_dir():
        return hash_directory(path)
    else:
        return hash_file(path)


def hash_json(obj: Any) -> str:
    """
    计算 JSON 对象的 hash（规范化后）。
    
    Args:
        obj: JSON 对象
    
    Returns:
        "sha256:..." 格式的 hash
    """
    canonical = canonicalize_json(obj)
    return hash_string(canonical)


def compute_inputs_fingerprint(
    required_keys: List[str],
    artifacts: Dict[str, Artifact],
) -> str:
    """
    计算 inputs fingerprint（基于上游 artifacts 的 fingerprint）。
    
    Args:
        required_keys: Phase 需要的 artifact keys（已排序）
        artifacts: manifest 中的 artifacts registry
    
    Returns:
        "sha256:..." 格式的 hash
    """
    # 按 key 排序，确保确定性
    sorted_keys = sorted(required_keys)
    
    # 构建 fingerprint 字符串：key1:fp1,key2:fp2,...
    fp_parts = []
    for key in sorted_keys:
        if key not in artifacts:
            raise ValueError(f"Required artifact '{key}' not found in manifest")
        artifact = artifacts[key]
        fp_parts.append(f"{key}:{artifact.fingerprint}")
    
    fp_string = ",".join(fp_parts)
    return hash_string(fp_string)


def compute_config_fingerprint(
    phase_name: str,
    config: Dict[str, Any],
) -> str:
    """
    计算 config fingerprint（hash 该 phase 的有效参数，包括全局配置中 phase 需要的部分）。
    
    Args:
        phase_name: Phase 名称
        config: 全局配置（包含 phases 配置）
    
    Returns:
        "sha256:..." 格式的 hash
    """
    # 提取该 phase 的配置
    phase_config = config.get("phases", {}).get(phase_name, {})
    
    # 提取 phase 需要的全局配置
    # 目前已知：extract, mix, burn 需要 video_path
    global_config_keys = []
    if phase_name in ("extract", "mix", "burn"):
        if "video_path" in config:
            global_config_keys.append("video_path")
    
    # 构建完整的配置字典（phase-specific + 需要的全局配置）
    full_config = dict(phase_config)
    for key in global_config_keys:
        if key in config:
            full_config[key] = config[key]
    
    # 规范化并 hash
    return hash_json(full_config)
