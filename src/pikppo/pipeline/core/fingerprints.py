"""
Fingerprint 计算：确定性 hash
"""
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from pikppo.pipeline.core.types import Artifact


def canonicalize_json(obj: Any) -> str:
    """
    规范化 JSON（排序 key、去 null、稳定浮点格式）。
    
    Args:
        obj: 要规范化的对象
    
    Returns:
        规范化后的 JSON 字符串
    """
    return json.dumps(
        obj,
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
    # 目前已知：demux, mix, burn 需要 video_path
    global_config_keys = []
    if phase_name in ("demux", "mix", "burn"):
        if "video_path" in config:
            global_config_keys.append("video_path")
    
    # 构建完整的配置字典（phase-specific + 需要的全局配置）
    full_config = dict(phase_config)
    for key in global_config_keys:
        if key in config:
            full_config[key] = config[key]
    
    # 规范化并 hash
    return hash_json(full_config)
