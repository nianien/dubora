"""
Export: AsrModel → SubtitleModel v1.3

复用 build_subtitle_model.py 的 semantic_split_text 和 calculate_speech_rate_zh_tps
做 cue 切分和语速计算。
"""
from typing import Dict, List, Optional

from dubora.schema.asr_model import AsrModel, AsrSegment
from dubora.schema.subtitle_model import (
    SubtitleModel,
    SubtitleUtterance,
    SubtitleCue,
    SourceText,
    SpeakerInfo,
    SpeechRate,
    SchemaInfo,
    EmotionInfo,
)
from dubora.schema.types import Word
from dubora.pipeline.processors.srt.build_subtitle_model import (
    semantic_split_text,
    calculate_speech_rate_zh_tps,
    normalize_speaker_id,
    build_emotion_info,
)


def export_subtitle_model(
    asr_model: AsrModel,
    *,
    roles_data: Optional[Dict] = None,
    source_lang: str = "zh",
    max_chars: int = 18,
    max_dur_ms: int = 2800,
    hard_punc: str = "\u3002\uff01\uff1f\uff1b",
    soft_punc: str = "\uff0c",
) -> SubtitleModel:
    """
    将 AsrModel 导出为 SubtitleModel v1.3。

    Args:
        asr_model: AsrModel 实例
        source_lang: 源语言代码
        max_chars: cue 最大字数
        max_dur_ms: cue 最大时长（毫秒）
        hard_punc: 硬标点（必切）
        soft_punc: 软标点（可切）

    Returns:
        SubtitleModel v1.3 实例
    """
    utterances: List[SubtitleUtterance] = []

    for idx, seg in enumerate(asr_model.segments, start=1):
        if not seg.text:
            continue

        # 构建 Word 列表（整段作为一个 word）
        words = _segment_to_words(seg)

        # 计算语速
        zh_tps = calculate_speech_rate_zh_tps(words) if words else 0.0

        # 构建 emotion
        emotion_info = build_emotion_info(seg.emotion) if seg.emotion != "neutral" else None

        # 获取 speaker gender（从 roles_data 查找，找不到 fallback "unknown"）
        gender = None
        if roles_data:
            roles = roles_data.get("roles", {})
            # roles_data.roles 的 value 是 voice_type，无直接 gender 信息
            # 暂时 fallback "unknown"
            gender = "unknown" if seg.speaker in roles else None

        # 语义切分 cues
        if words:
            cue_data_list = semantic_split_text(
                text=seg.text,
                words=words,
                max_chars=max_chars,
                max_dur_ms=max_dur_ms,
                hard_punc=hard_punc,
                soft_punc=soft_punc,
            )
        else:
            cue_data_list = [(seg.text, seg.start_ms, seg.end_ms)]

        cues: List[SubtitleCue] = []
        for cue_text, cue_start_ms, cue_end_ms in cue_data_list:
            cues.append(SubtitleCue(
                start_ms=int(cue_start_ms),
                end_ms=int(cue_end_ms),
                source=SourceText(lang=source_lang, text=str(cue_text)),
            ))

        if not cues:
            continue

        normalized_speaker = normalize_speaker_id(seg.speaker)

        utterances.append(SubtitleUtterance(
            utt_id=f"utt_{idx:04d}",
            speaker=SpeakerInfo(
                id=normalized_speaker,
                gender=gender if gender != "unknown" else None,
                speech_rate=SpeechRate(zh_tps=float(zh_tps)),
                emotion=emotion_info,
            ),
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            cues=cues,
        ))

    # audio 元数据
    audio = None
    if asr_model.media.duration_ms:
        audio = {"duration_ms": asr_model.media.duration_ms}
    elif utterances:
        audio = {"duration_ms": utterances[-1].end_ms}

    return SubtitleModel(
        schema=SchemaInfo(name="subtitle.model", version="1.3"),
        audio=audio,
        utterances=utterances,
    )


def _segment_to_words(seg: AsrSegment) -> List[Word]:
    """从 AsrSegment 构建 Word 列表（整段作为一个 word）。"""
    if not seg.text.strip():
        return []
    return [Word(
        start_ms=seg.start_ms,
        end_ms=seg.end_ms,
        text=seg.text,
        speaker=seg.speaker,
    )]
