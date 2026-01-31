"""
Subtitle Model: 字幕系统的唯一事实源（SSOT）v1.2

极简 SSOT 设计原则：
- SSOT：唯一真相，只保存原始事实，不包含任何翻译或执行信息
- 最小字段集：只保留会被下游用到的
- 结构明确：utterance 层包含语速信息，cue 层只包含原始字幕事实
- 不把 raw/additions 塞进模型（那是 ASR 原始事实，不是 SSOT）

v1.2 升级：
- 移除 cue.target 字段（翻译信息不属于 SSOT）
- 添加 utterance 层，包含 speech_rate.zh_tps（语速信息）
- SSOT 只保存原始事实，不包含任何翻译或执行信息

各阶段职责（ownership 清晰）：
- asr_post：写 speakers、utterances、cues[*].source、start/end/speaker、emotion(可选)、speech_rate
- mt：不写 SSOT（翻译结果单独保存）
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
    - version: Schema 版本（如 "1.2"）
    """
    name: str = "subtitle.model"
    version: str = "1.2"


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
    - emotion: 情绪信息（可选，用于 TTS style hint）
    
    注意：
    - source 必须存在（asr_post 阶段填写）
    - emotion 可选（无/低置信度就省略）
    - v1.2: 移除 target 字段（翻译信息不属于 SSOT）
    """
    cue_id: str
    start_ms: int
    end_ms: int
    speaker: str
    source: SourceText
    emotion: Optional[EmotionInfo] = None


@dataclass
class SpeechRate:
    """
    语速信息。
    
    字段：
    - zh_tps: 中文 tokens per second（每秒 token 数）
    
    计算规则：
    - token 来源：ASR word / char timestamps
    - 丢弃：start_ms < 0 或 end_ms < 0，空白 token
    - 合并 token 时间区间（union）
    - 只把最终 zh_tps 数值写入 SSOT
    """
    zh_tps: float


@dataclass
class SubtitleUtterance:
    """
    连续说话单元（utterance）。
    
    字段：
    - utt_id: Utterance ID（唯一标识）
    - speaker: 说话人标识（规范化后，如 "spk_1"）
    - start_ms: 开始时间（毫秒）
    - end_ms: 结束时间（毫秒）
    - speech_rate: 语速信息（zh_tps）
    - cues: 该 utterance 包含的 cues 列表
    
    约束（硬规则）：
    - start_ms == cues[0].start_ms
    - end_ms == cues[last].end_ms
    - speech_rate.zh_tps 必须存在
    - utterances 不允许时间重叠
    
    注意：
    - 这是 Subtitle Model v1.2 中的 Utterance，与 schema/types.py 中的 Utterance（ASR 原始响应）不同
    - 为避免命名冲突，使用 SubtitleUtterance 名称
    """
    utt_id: str
    speaker: str
    start_ms: int
    end_ms: int
    speech_rate: SpeechRate
    cues: List["SubtitleCue"] = field(default_factory=list)


@dataclass
class SubtitleModel:
    """
    Subtitle Model：字幕系统的唯一事实源（SSOT）v1.2。
    
    极简 SSOT 设计：
    - 最小字段集：只保留会被下游用到的
    - 结构明确：utterance 层包含语速信息，cue 层只包含原始字幕事实
    - 不把 raw/additions 塞进模型（那是 ASR 原始事实，不是 SSOT）
    - SSOT 只保存原始事实，不包含任何翻译或执行信息
    
    字段：
    - schema: Schema 元信息
    - audio: 音频元数据（duration_ms）
    - utterances: 连续说话单元列表（包含语速信息）
    
    各阶段职责（ownership 清晰）：
    - asr_post：写 utterances、cues[*].source、start/end/speaker、emotion(可选)、speech_rate
    - mt：不写 SSOT（翻译结果单独保存）
    - tts：不写 SSOT（只读生成 tts_jobs）
    """
    schema: SchemaInfo = field(default_factory=lambda: SchemaInfo())
    audio: Optional[Dict[str, Any]] = None  # duration_ms, etc.
    utterances: List[SubtitleUtterance] = field(default_factory=list)
