"""
Schema: Provider-agnostic data contracts shared across the pipeline.

核心概念：
- Utterance: ASR 返回的原始话语单元（带 speaker）
- Segment: 已完成 speaker-aware 切分/合并，但还未格式化为 SRT（仍带 speaker）
- SrtCue: 最终用于 SRT 格式化的字幕单元（不带 speaker）
- Word: 词级别信息（用于 word-gap 切分）

这些类型是通用的，不绑定到特定的 ASR provider（doubao/gemini/whisper 等）。

依赖规则：
- schema 只能被依赖，不能依赖 models 或 processors
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Word:
    """
    ASR 返回的单个词/字级别信息。
    
    字段：
    - start_ms: 开始时间（毫秒）
    - end_ms: 结束时间（毫秒）
    - text: 词/字文本
    - speaker: 说话人标识（可选，如果 word 级别有 speaker 信息）
    """
    start_ms: int
    end_ms: int
    text: str
    speaker: str = ""  # 可选，word 级别可能没有 speaker


@dataclass
class Utterance:
    """
    ASR 返回的原始话语单元。
    
    字段：
    - speaker: 说话人标识（字符串，如 "0", "1"）
    - start_ms: 开始时间（毫秒）
    - end_ms: 结束时间（毫秒）
    - text: 文本内容
    - words: 词级别信息列表（可选，用于 word-gap 切分）
    - emotion: 情绪标签（可选，如 "sad", "happy"）
    - gender: 性别标签（可选，如 "male", "female"）
    """
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    words: Optional[List["Word"]] = None  # 可选，用于 word-gap 切分
    emotion: Optional[str] = None  # 可选，情绪标签
    gender: Optional[str] = None  # 可选，性别标签


@dataclass
class Segment:
    """
    已完成 speaker-aware 切分/合并的中间数据结构。
    
    重要：这是 postprocess 的输出，但还未格式化为 SRT。
    仍然保留 speaker 字段，用于：
    - 内部逻辑验证（确保不混 speaker）
    - 未来可能的 speaker 相关处理
    
    字段：
    - speaker: 说话人标识（必须单一，不能混）
    - start_ms: 开始时间（毫秒）
    - end_ms: 结束时间（毫秒）
    - text: 文本内容（不包含 [speaker] 标签）
    - emotion: 情绪标签（可选，如 "sad", "happy"）
    - gender: 性别标签（可选，如 "male", "female"）
    
    规则：
    - 每条 Segment 只对应一个 speaker
    - 不同 speaker 的 Segment 不能合并
    - speaker 字段在转换为 SrtCue 时会被丢弃
    - emotion/gender 从对应的 Utterance 继承（如果 Segment 由多个 Utterance 合并，使用第一个）
    """
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    emotion: Optional[str] = None  # 可选，情绪标签
    gender: Optional[str] = None  # 可选，性别标签


# 别名：SubtitleSegment = Segment（语义更清晰）
SubtitleSegment = Segment


@dataclass
class SrtCue:
    """
    最终用于 SRT 格式化的字幕单元。
    
    注意：不包含 speaker 字段，因为 SRT 输出不显示说话人标识。
    
    字段：
    - start_ms: 开始时间（毫秒）
    - end_ms: 结束时间（毫秒）
    - text: 文本内容（纯文本，无 speaker 标签）
    """
    start_ms: int
    end_ms: int
    text: str


# 类型别名（便于使用）
WordList = List[Word]
UtteranceList = List[Utterance]
SegmentList = List[Segment]
SrtCueList = List[SrtCue]
