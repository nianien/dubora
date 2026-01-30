"""
构建 Subtitle Model：从 Segment[] 生成真正的 Subtitle Model (SSOT) v1.1

职责：
- 将 asr_post.py 生成的 Segment[] 转换为 Subtitle Model v1.1
- 规范化 speaker ID（"1" → "spk_1"）
- 保留完整的 emotion 语义（不丢失 confidence/intensity）
- 构建 speakers 实体定义（最小必需字段）

架构原则：
- 这是唯一可以构建 Subtitle Model 的地方（asr_post 阶段）
- Subtitle Model 是 SSOT，包含完整语义

各阶段职责（ownership 清晰）：
- asr_post：写 speakers、cues[*].source、start/end/speaker、emotion(可选)
- mt：只写 cues[*].target
- tts：不写 SSOT（只读生成 tts_jobs）
"""
from typing import Any, Dict, List, Optional

from pikppo.schema import Segment
from pikppo.schema.subtitle_model import (
    SubtitleModel,
    SubtitleCue,
    SpeakerInfo,
    EmotionInfo,
    SourceText,
)


def normalize_speaker_id(speaker: str) -> str:
    """
    规范化 speaker ID。
    
    Args:
        speaker: 原始 speaker ID（如 "1", "2", "speaker_0"）
    
    Returns:
        规范化后的 speaker ID（如 "spk_1", "spk_2"）
    """
    # 如果已经是规范化格式，直接返回
    if speaker.startswith("spk_"):
        return speaker
    
    # 提取数字部分
    import re
    match = re.search(r'\d+', speaker)
    if match:
        num = match.group()
        return f"spk_{num}"
    
    # 兜底：直接加前缀
    return f"spk_{speaker}"


def build_emotion_info(
    emotion_label: Optional[str],
    emotion_score: Optional[float] = None,
    emotion_degree: Optional[str] = None,
) -> Optional[EmotionInfo]:
    """
    构建 EmotionInfo（用于 TTS style hint）。
    
    Args:
        emotion_label: 情绪标签（如 "sad", "happy", "neutral"）
        emotion_score: 置信度（0.0-1.0）
        emotion_degree: 情绪强度（如 "weak", "strong"）
    
    Returns:
        EmotionInfo 或 None（如果 emotion_label 为空或置信度太低）
    
    注意：
    - 无/低置信度就省略或写 neutral
    - 如果 emotion_label 为空，返回 None
    """
    if not emotion_label:
        return None
    
    # 如果置信度太低（< 0.5），可以降级为 neutral 或省略
    # 这里保留原始 label，让 TTS 阶段决定如何处理
    return EmotionInfo(
        label=emotion_label,
        confidence=emotion_score,
        intensity=emotion_degree,  # 使用 intensity 而不是 degree
    )


def build_subtitle_model(
    segments: List[Segment],
    raw_response: Dict[str, Any],
    source_lang: str = "zh",  # 默认源语言为中文
    audio_duration_ms: Optional[int] = None,
) -> SubtitleModel:
    """
    从 Segment[] 构建 Subtitle Model v1.1（SSOT）。
    
    Args:
        segments: 切分后的 segments（来自 asr_post.py）
        raw_response: ASR 原始响应（SSOT，用于提取完整的 emotion 信息，包含 score/degree）
        source_lang: 源语言代码（如 "zh", "en"），默认 "zh"
        audio_duration_ms: 音频时长（毫秒，可选）
    
    Returns:
        SubtitleModel: 完整的字幕模型 v1.1（SSOT）
    
    注意：
    - 从 raw_response 中提取完整的 emotion 信息（包含 score/intensity）
    - raw_response 是 SSOT，包含完整语义信息
    - 只填写 source，target 由 MT 阶段填写
    """
    # 1. 收集所有 speaker，构建 speakers 实体定义（最小必需字段）
    speaker_set = set()
    for seg in segments:
        normalized_id = normalize_speaker_id(seg.speaker)
        speaker_set.add(normalized_id)
    
    speakers: Dict[str, SpeakerInfo] = {}
    for spk_id in sorted(speaker_set):
        speakers[spk_id] = SpeakerInfo(
            speaker_id=spk_id,
            voice_id=None,  # 后续由 TTS phase 分配
        )
    
    # 2. 构建 cues（从 segments 转换）
    # 从 raw_response 中提取完整的 emotion 信息
    result = raw_response.get("result") or {}
    uts = result.get("utterances") or []
    utterance_additions_map: Dict[str, Dict[str, Any]] = {}
    for utt in uts:
        # 使用时间范围作为 key（简化匹配）
        key = f"{utt.get('start_time', 0)}_{utt.get('end_time', 0)}"
        additions = utt.get("additions") or {}
        utterance_additions_map[key] = additions
    
    cues: List[SubtitleCue] = []
    for i, seg in enumerate(segments):
        # 规范化 speaker ID
        normalized_speaker = normalize_speaker_id(seg.speaker)
        
        # 尝试从 raw_response 中提取完整的 emotion 信息
        emotion_label = seg.emotion
        emotion_score = None
        emotion_degree = None
        
        # 尝试匹配原始 utterance 以获取完整的 additions 信息
        # 简化匹配：使用 segment 的时间范围查找对应的 utterance
        for key, additions in utterance_additions_map.items():
            # 检查时间范围是否重叠（简化匹配）
            utt_start = int(key.split("_")[0])
            utt_end = int(key.split("_")[1])
            if seg.start_ms >= utt_start and seg.end_ms <= utt_end:
                # 找到匹配的 utterance，提取完整的 emotion 信息
                if additions.get("emotion"):
                    emotion_label = additions.get("emotion")
                    emotion_score = additions.get("emotion_score")
                    if emotion_score:
                        try:
                            emotion_score = float(emotion_score)
                        except (ValueError, TypeError):
                            emotion_score = None
                    emotion_degree = additions.get("emotion_degree")
                break
        
        # 构建 emotion info（可选）
        emotion_info: Optional[EmotionInfo] = None
        if emotion_label:
            emotion_info = build_emotion_info(
                emotion_label=emotion_label,
                emotion_score=emotion_score,
                emotion_degree=emotion_degree,
            )
        
        # 生成 cue_id
        cue_id = f"cue_{i+1:04d}"
        
        # 构建 source text（asr_post 阶段填写）
        source = SourceText(
            lang=source_lang,
            text=seg.text,
        )
        
        # target 由 MT 阶段填写，这里为 None
        cues.append(SubtitleCue(
            cue_id=cue_id,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            speaker=normalized_speaker,
            source=source,
            target=None,  # MT 阶段填写
            emotion=emotion_info,  # 可选，用于 TTS style hint
        ))
    
    # 3. 构建 audio 元数据
    audio: Optional[Dict[str, Any]] = None
    if audio_duration_ms is not None:
        audio = {
            "duration_ms": audio_duration_ms,
        }
    elif cues:
        # 从最后一个 cue 推断时长
        last_cue = cues[-1]
        audio = {
            "duration_ms": last_cue.end_ms,
        }
    
    # 4. 构建 Subtitle Model v1.1
    from pikppo.schema.subtitle_model import SchemaInfo
    model = SubtitleModel(
        schema=SchemaInfo(name="subtitle.model", version="1.1"),
        audio=audio,
        speakers=speakers,
        cues=cues,
    )
    
    return model
