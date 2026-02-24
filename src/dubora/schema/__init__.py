"""
Schema: Provider-agnostic data contracts shared across the pipeline.

职责：
- 定义通用的数据结构（Word, Utterance, Segment, SrtCue）
- 跨模块契约（DTO / schema）
- 不包含业务逻辑
- 不包含调度、编排、策略

依赖规则：
- schema 只能被依赖，不能依赖 models 或 processors
- models 和 processors 都依赖 schema，但彼此不依赖

This defines provider-agnostic data contracts shared across the pipeline.
It must not depend on models or processors.
"""
from .types import (
    Word,
    Utterance,
    Segment,
    SrtCue,
    SubtitleSegment,
    WordList,
    UtteranceList,
    SegmentList,
    SrtCueList,
)
from .asr_fix import (
    AsrFix,
    AsrFixUtterance,
)
from .subtitle_model import (
    SubtitleModel,
    SubtitleCue,
    SubtitleUtterance,
    SpeakerInfo,
    EmotionInfo,
    SourceText,
    SpeechRate,
    SchemaInfo,
)

__all__ = [
    # ASR Model（人工校准层）
    "AsrFix",
    "AsrFixUtterance",
    "Word",
    "Utterance",  # ASR 原始响应（schema/types.py）
    "Segment",
    "SrtCue",
    "SubtitleSegment",
    "WordList",
    "UtteranceList",
    "SegmentList",
    "SrtCueList",
    # Subtitle Model v1.3
    "SubtitleModel",
    "SubtitleCue",
    "SubtitleUtterance",  # Subtitle Model 中的 Utterance（避免与 ASR Utterance 冲突）
    "SpeakerInfo",
    "EmotionInfo",
    "SourceText",
    "SpeechRate",
    "SchemaInfo",
]
