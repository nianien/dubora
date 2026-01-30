"""
Subtitle Model: 字幕系统的唯一事实源（SSOT）v1.1

极简 SSOT 设计原则：
- SSOT：唯一真相，后续阶段都只读/只补充自己字段
- 最小字段集：只保留会被下游用到的
- 结构明确：source/target 分离，避免未来混乱
- 不把 raw/additions 塞进模型（那是 ASR 原始事实，不是 SSOT）

各阶段职责（ownership 清晰）：
- asr_post：写 speakers、cues[*].source、start/end/speaker、emotion(可选)
- mt：只写 cues[*].target
- tts：不写 SSOT（只读生成 tts_jobs）
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SchemaInfo:
    """
    Schema 元信息。
    
    字段：
    - name: Schema 名称（如 "subtitle.model"）
    - version: Schema 版本（如 "1.1"）
    """
    name: str = "subtitle.model"
    version: str = "1.1"


@dataclass
class SourceText:
    """
    源文本（原文）。
    
    字段：
    - lang: 语言代码（如 "zh", "en"）
    - text: 文本内容
    """
    lang: str
    text: str


@dataclass
class TargetText:
    """
    目标文本（译文）。
    
    字段：
    - lang: 语言代码（如 "en", "zh"）
    - text: 文本内容
    """
    lang: str
    text: str


@dataclass
class EmotionInfo:
    """
    情绪信息（用于 TTS style hint）。
    
    字段：
    - label: 情绪标签（如 "sad", "happy", "neutral"）
    - confidence: 置信度（0.0-1.0，可选）
    - intensity: 情绪强度（如 "weak", "strong"，可选）
    
    注意：
    - 无/低置信度就省略或写 neutral
    - 可选字段，如果不存在则省略
    """
    label: str
    confidence: Optional[float] = None
    intensity: Optional[str] = None


@dataclass
class SpeakerInfo:
    """
    说话人实体定义（最小必需字段）。
    
    字段：
    - speaker_id: 说话人标识（规范化后，如 "spk_1"）
    - voice_id: 声线 ID（可选，用于 TTS，可为 null，但要有 fallback 策略）
    
    注意：
    - 不强制 age/gender/profile（可外置 registry）
    - voice_id 在 TTS 阶段分配
    """
    speaker_id: str
    voice_id: Optional[str] = None


@dataclass
class SubtitleCue:
    """
    字幕单元（Subtitle Model 中的核心结构）。
    
    字段：
    - cue_id: 字幕单元 ID（唯一标识）
    - start_ms: 开始时间（毫秒）
    - end_ms: 结束时间（毫秒）
    - speaker: 说话人标识（规范化后，如 "spk_1"）
    - source: 源文本（原文，asr_post 阶段填写）
    - target: 目标文本（译文，MT 阶段填写，可选）
    - emotion: 情绪信息（可选，用于 TTS style hint）
    
    注意：
    - source 必须存在（asr_post 阶段填写）
    - target 可选（MT 阶段填写）
    - emotion 可选（无/低置信度就省略）
    """
    cue_id: str
    start_ms: int
    end_ms: int
    speaker: str
    source: SourceText
    target: Optional[TargetText] = None
    emotion: Optional[EmotionInfo] = None


@dataclass
class SubtitleModel:
    """
    Subtitle Model：字幕系统的唯一事实源（SSOT）v1.1。
    
    极简 SSOT 设计：
    - 最小字段集：只保留会被下游用到的
    - 结构明确：source/target 分离，避免未来混乱
    - 不把 raw/additions 塞进模型（那是 ASR 原始事实，不是 SSOT）
    
    字段：
    - schema: Schema 元信息
    - audio: 音频元数据（duration_ms）
    - speakers: 说话人实体定义（speaker_id -> SpeakerInfo）
    - cues: 字幕单元列表
    
    各阶段职责（ownership 清晰）：
    - asr_post：写 speakers、cues[*].source、start/end/speaker、emotion(可选)
    - mt：只写 cues[*].target
    - tts：不写 SSOT（只读生成 tts_jobs）
    """
    schema: SchemaInfo = field(default_factory=lambda: SchemaInfo())
    audio: Optional[Dict[str, Any]] = None  # duration_ms, etc.
    speakers: Dict[str, SpeakerInfo] = field(default_factory=dict)
    cues: List[SubtitleCue] = field(default_factory=list)
