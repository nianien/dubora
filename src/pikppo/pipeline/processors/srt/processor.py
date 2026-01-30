"""
Subtitle Processor: 字幕后处理（唯一对外入口）

职责：
- 接收 Phase 层的输入（raw_response，SSOT）
- 从 raw_response 解析为 Utterance[]，然后生成 Subtitle Model (SubtitleModel)
- 返回 ProcessorResult（不负责文件 IO、不生成格式文件）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在 impl.py, asr_post.py, profiles.py, build_subtitle_model.py
- Phase 层只调用 processor.run()
- processor 只生成 Subtitle Model，不生成任何格式文件（SRT/VTT）

Subtitle Model 是 SSOT（唯一事实源）：
- 直接从 raw_response 生成（保留完整语义信息）
- asr_post.py 生成 Segment[]（中间态）
- build_subtitle_model.py 将 Segment[] 转换为 SubtitleModel（SSOT）
- 任何字幕文件（SRT/VTT）均为 Subtitle Model 的派生视图
- 下游模块（render_srt.py）负责格式渲染
"""
from typing import Any, Dict, Optional

from .._types import ProcessorResult
from .impl import process_raw_response_to_segments
from .build_subtitle_model import build_subtitle_model
from pikppo.schema.subtitle_model import SubtitleModel


def run(
    raw_response: Dict[str, Any],
    *,
    postprofile: str = "axis",
    audio_duration_ms: Optional[int] = None,
) -> ProcessorResult:
    """
    从 raw_response 生成 Subtitle Model (SubtitleModel)。
    
    Args:
        raw_response: ASR 原始响应（SSOT，包含完整语义信息）
        postprofile: 字幕策略名称（axis, axis_default, axis_soft）
        audio_duration_ms: 音频时长（毫秒，可选）
    
    Returns:
        ProcessorResult:
        - data.subtitle_model: Subtitle Model (SubtitleModel，SSOT)
        - data.segments: Segment[]（向后兼容，可选）
        - metrics: 元数据（utterances_count, cues_count, speakers_count 等）
    
    注意：
    - 直接从 raw_response 生成（SSOT，保留完整语义信息）
    - 生成真正的 Subtitle Model（包含 audio, speakers, cues 等完整结构）
    - SRT/VTT 文件由 Phase 层调用 render_srt.py / render_vtt.py 生成
    """
    # Step 1: 从 raw_response 生成 Segment[]（中间态）
    segments = process_raw_response_to_segments(
        raw_response=raw_response,
        postprofile=postprofile,
    )
    
    # Step 2: 构建 Subtitle Model v1.1 (SSOT)
    subtitle_model = build_subtitle_model(
        segments=segments,
        raw_response=raw_response,  # 传递原始响应以提取完整的 emotion 信息（包含 score/intensity）
        source_lang="zh",  # 默认源语言为中文
        audio_duration_ms=audio_duration_ms,
    )
    
    # 从 raw_response 提取 utterances 数量（用于 metrics）
    result = raw_response.get("result") or {}
    utterances_count = len(result.get("utterances") or [])
    
    return ProcessorResult(
        outputs=[],  # 由 Phase 声明 outputs，processor 只负责业务处理
        data={
            "subtitle_model": subtitle_model,  # Subtitle Model (SSOT)
            "segments": segments,  # 向后兼容（可选）
        },
        metrics={
            "utterances_count": utterances_count,
            "segments_count": len(segments),
            "cues_count": len(subtitle_model.cues),
            "speakers_count": len(subtitle_model.speakers),
            "postprofile": postprofile,
        },
    )
