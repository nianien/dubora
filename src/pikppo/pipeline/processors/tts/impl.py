"""
TTS Processor 内部实现

职责：
- 声线分配逻辑
- TTS 合成逻辑
- 不负责文件 IO（由 Phase 层负责）
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .assign_voices import assign_voices as _assign_voices_impl
from .azure import synthesize_tts as _synthesize_tts_impl


def assign_voices_to_segments(
    segments: List[Dict[str, Any]],
    *,
    reference_audio_path: Optional[str] = None,
    voice_pool_path: Optional[str] = None,
) -> Dict[str, str]:
    """
    为每个 speaker 分配 voice（纯逻辑，不涉及文件 IO）。
    
    Args:
        segments: segments 列表
        reference_audio_path: 参考音频路径（可选，用于性别检测）
        voice_pool_path: voice pool JSON 文件路径（可选）
    
    Returns:
        speaker -> voice_id 映射字典
    """
    # 这里需要重构 assign_voices 的内部逻辑
    # 暂时保留原函数调用，但后续应该提取核心逻辑
    # TODO: 重构 assign_voices 以分离文件 IO 和业务逻辑
    raise NotImplementedError("需要重构 assign_voices 以分离文件 IO")


def synthesize_segments_to_audio(
    segments: List[Dict[str, Any]],
    voice_assignment: Dict[str, str],
    *,
    azure_key: str,
    azure_region: str,
    language: str = "en-US",
    max_workers: int = 4,
) -> bytes:
    """
    将 segments 合成为音频（纯逻辑，返回音频数据）。
    
    Args:
        segments: segments 列表
        voice_assignment: speaker -> voice_id 映射
        azure_key: Azure TTS key
        azure_region: Azure region
        language: 语言代码
        max_workers: 最大并发数
    
    Returns:
        音频数据（bytes）
    """
    # 这里需要重构 synthesize_tts 的内部逻辑
    # 暂时保留原函数调用，但后续应该提取核心逻辑
    # TODO: 重构 synthesize_tts 以分离文件 IO 和业务逻辑
    raise NotImplementedError("需要重构 synthesize_tts 以分离文件 IO")
