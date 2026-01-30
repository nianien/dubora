"""
Subtitle Processor 内部实现

职责：
- 从 raw_response 生成 segments（核心业务逻辑）
- 不负责文件 IO
"""
from typing import Any, Dict, List

from pikppo.schema import Segment
from pikppo.models.doubao.parser import parse_utterances
from .asr_post import speaker_aware_postprocess
from .profiles import POSTPROFILES


def process_raw_response_to_segments(
    raw_response: Dict[str, Any],
    *,
    postprofile: str = "axis",
) -> List[Segment]:
    """
    从 raw_response 生成 segments（核心业务逻辑）。
    
    Args:
        raw_response: ASR 原始响应（SSOT，包含完整语义信息）
        postprofile: 字幕策略名称
    
    Returns:
        Segment 列表（已清理首尾标点）
    
    Raises:
        KeyError: 如果 postprofile 不存在
    """
    if postprofile not in POSTPROFILES:
        raise KeyError(
            f"未知的字幕策略: {postprofile}\n"
            f"可用策略: {', '.join(sorted(POSTPROFILES.keys()))}"
        )
    
    # 从 raw_response 解析为 Utterance[]
    utterances = parse_utterances(raw_response)
    
    if not utterances:
        return []
    
    # 应用后处理策略生成 segments
    segments = speaker_aware_postprocess(
        utterances=utterances,
        profile_name=postprofile,
        profiles=POSTPROFILES,
    )
    
    if not segments:
        return []
    
    return segments
