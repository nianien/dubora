"""
构建 Subtitle Model v1.2（SSOT）

职责：
- 从 asr_result.json 提取 word-level timestamps
- 使用 Utterance Normalization 重建视觉友好的 utterance 边界（真正的 SSOT）
- 按照语义切分 cues
- 根据 words 的时间轴生成 cue 的时间轴

核心理念：
- ASR raw utterances 不是 SSOT（模型导向，不是视觉友好）
- 真正的 SSOT 基于 speech + silence 重建
"""
from typing import Any, Dict, List, Optional, Tuple
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
from .text_word_alignment import align_corrected_text_to_words, _compute_lcs_alignment
from .utterance_normalization import (
    normalize_utterances,
    extract_all_words_from_raw_response,
    extract_utterance_metadata,
    NormalizationConfig,
    NormalizedUtterance,
)


def normalize_speaker_id(speaker: str) -> str:
    """规范化 speaker ID：去空白，空值返回 "0"。"""
    if not speaker:
        return "0"
    speaker = str(speaker).strip()
    # 兼容旧数据：去掉历史遗留的 spk_ 前缀
    if speaker.startswith("spk_"):
        speaker = speaker[4:]
    return speaker


def build_emotion_info(
    emotion_label: Optional[str],
    emotion_score: Optional[float] = None,
    emotion_degree: Optional[str] = None,
) -> Optional[EmotionInfo]:
    """构建 EmotionInfo 对象。"""
    if not emotion_label:
        return None

    return EmotionInfo(
        label=str(emotion_label),
        confidence=float(emotion_score) if emotion_score is not None else None,
        intensity=str(emotion_degree) if emotion_degree else None,
    )


def calculate_speech_rate_zh_tps(words: List[Word]) -> float:
    """
    计算语速（中文 tokens per second）。
    
    规则：
    - 只统计有效的 words（start_ms >= 0, end_ms >= 0, text 非空）
    - 合并 words 的时间区间（union）
    - zh_tps = 有效 token 数 / 发声秒数
    """
    if not words:
        return 0.0
    
    # 过滤有效 words
    valid_words = []
    for w in words:
        if w.start_ms >= 0 and w.end_ms >= 0 and w.start_ms < w.end_ms:
            text = str(w.text).strip()
            if text:
                valid_words.append(w)
    
    if not valid_words:
        return 0.0
    
    # 合并时间区间（union）
    intervals = [(w.start_ms, w.end_ms) for w in valid_words]
    intervals.sort(key=lambda x: (x[0], x[1]))
    
    merged = []
    for start, end in intervals:
        if not merged:
            merged.append((start, end))
        else:
            last_start, last_end = merged[-1]
            if start <= last_end:
                # 重叠或相邻，合并
                merged[-1] = (last_start, max(last_end, end))
            else:
                # 不重叠，添加新区间
                merged.append((start, end))
    
    # 计算总发声时间（秒）
    total_duration_ms = sum(end - start for start, end in merged)
    total_duration_s = total_duration_ms / 1000.0
    
    if total_duration_s <= 0:
        return 0.0
    
    # 计算有效 token 数（中文字符数）
    total_chars = sum(len(str(w.text).strip()) for w in valid_words)
    
    # zh_tps = token 数 / 秒
    zh_tps = total_chars / total_duration_s
    
    return zh_tps


def semantic_split_text(
    text: str,
    words: List[Word],
    max_chars: int = 18,
    max_dur_ms: int = 2800,
    hard_punc: str = "。！？；",
    soft_punc: str = "，",
) -> List[Tuple[str, int, int]]:
    """
    按照语义切分文本，返回 (text, start_ms, end_ms) 列表。
    
    Args:
        text: 要切分的文本
        words: 词列表（用于计算时间戳）
        max_chars: 最大字数阈值
        max_dur_ms: 最大时长阈值（毫秒）
        hard_punc: 硬标点（必切）
        soft_punc: 软标点（可切）
    
    Returns:
        List[Tuple[str, int, int]]: (cue_text, start_ms, end_ms) 列表
    """
    if not text or not words:
        # 如果没有文本或 words，返回整个 utterance 的时间范围
        if words:
            start_ms = words[0].start_ms
            end_ms = words[-1].end_ms
        else:
            start_ms = 0
            end_ms = 0
        return [(text, start_ms, end_ms)]
    
    cues: List[Tuple[str, int, int]] = []
    text_pos = 0
    word_idx = 0
    
    # 计算 utterance 的总时长
    utt_start_ms = words[0].start_ms
    utt_end_ms = words[-1].end_ms
    
    while text_pos < len(text):
        remaining = text[text_pos:]
        
        # 如果剩余文本不足 max_chars，直接作为最后一个 cue
        if len(remaining) <= max_chars:
            # 找到对应的 words 范围
            cue_start_ms, cue_end_ms = _get_timestamps_for_text(
                remaining, words, word_idx, utt_start_ms, utt_end_ms
            )
            cues.append((remaining, cue_start_ms, cue_end_ms))
            break
        
        # 优先在硬标点处切
        hard_cut = -1
        for p in hard_punc:
            idx = remaining.find(p, 0, max_chars + 1)
            if idx > 0 and (hard_cut < 0 or idx < hard_cut):
                hard_cut = idx
        
        if hard_cut > 0:
            cue_text = remaining[:hard_cut + 1]
            cue_start_ms, cue_end_ms = _get_timestamps_for_text(
                cue_text, words, word_idx, utt_start_ms, utt_end_ms
            )
            cues.append((cue_text, cue_start_ms, cue_end_ms))
            text_pos += hard_cut + 1
            word_idx = _advance_word_idx(cue_text, words, word_idx)
            continue
        
        # 如果超过 max_chars，找软标点
        soft_cut = -1
        for p in soft_punc:
            idx = remaining.rfind(p, 0, max_chars + 1)
            if idx > 0 and (soft_cut < 0 or idx > soft_cut):
                soft_cut = idx
        
        if soft_cut > 0:
            cue_text = remaining[:soft_cut + 1]
            cue_start_ms, cue_end_ms = _get_timestamps_for_text(
                cue_text, words, word_idx, utt_start_ms, utt_end_ms
            )
            cues.append((cue_text, cue_start_ms, cue_end_ms))
            text_pos += soft_cut + 1
            word_idx = _advance_word_idx(cue_text, words, word_idx)
            continue
        
        # 没有标点，按字数硬切
        cue_text = remaining[:max_chars]
        cue_start_ms, cue_end_ms = _get_timestamps_for_text(
            cue_text, words, word_idx, utt_start_ms, utt_end_ms
        )
        cues.append((cue_text, cue_start_ms, cue_end_ms))
        text_pos += max_chars
        word_idx = _advance_word_idx(cue_text, words, word_idx)
    
    return cues if cues else [(text, utt_start_ms, utt_end_ms)]


def _get_timestamps_for_text(
    cue_text: str,
    words: List[Word],
    word_idx: int,
    fallback_start: int,
    fallback_end: int,
) -> Tuple[int, int]:
    """
    根据文本内容找到对应的 words 范围，返回时间戳。
    
    Args:
        cue_text: cue 的文本
        words: 词列表
        word_idx: 起始 word 索引
        fallback_start: 回退开始时间
        fallback_end: 回退结束时间
    
    Returns:
        (start_ms, end_ms)
    """
    if not words or word_idx >= len(words):
        return (fallback_start, fallback_end)
    
    # 找到 cue_text 对应的 words 范围
    text_consumed = 0
    start_word_idx = word_idx
    end_word_idx = word_idx
    
    for i in range(word_idx, len(words)):
        w = words[i]
        w_text = str(w.text).strip()
        if text_consumed + len(w_text) <= len(cue_text):
            text_consumed += len(w_text)
            end_word_idx = i + 1
        else:
            break
    
    if start_word_idx < len(words) and end_word_idx > start_word_idx:
        cue_start_ms = words[start_word_idx].start_ms
        cue_end_ms = words[end_word_idx - 1].end_ms
        return (cue_start_ms, cue_end_ms)
    
    # 回退：按字符比例分配时间
    if word_idx < len(words):
        total_text_len = sum(len(str(w.text).strip()) for w in words[word_idx:])
        if total_text_len > 0:
            ratio = len(cue_text) / total_text_len
            dur = fallback_end - fallback_start
            cue_start_ms = fallback_start
            cue_end_ms = fallback_start + int(dur * ratio)
            return (cue_start_ms, cue_end_ms)
    
    return (fallback_start, fallback_end)


def _advance_word_idx(cue_text: str, words: List[Word], word_idx: int) -> int:
    """
    根据 cue_text 推进 word_idx。
    
    Args:
        cue_text: cue 的文本
        words: 词列表
        word_idx: 当前 word 索引
    
    Returns:
        新的 word_idx
    """
    text_consumed = 0
    for i in range(word_idx, len(words)):
        text_consumed += len(str(words[i].text).strip())
        if text_consumed >= len(cue_text):
            return i + 1
    return len(words)


def build_subtitle_model(
    raw_response: Dict[str, Any],
    *,
    source_lang: str = "zh",
    audio_duration_ms: Optional[int] = None,
    max_chars: int = 18,
    max_dur_ms: int = 2800,
    hard_punc: str = "。！？；",
    soft_punc: str = "，",
    # Utterance Normalization 配置
    silence_split_threshold_ms: int = 450,
    min_utterance_duration_ms: int = 900,
    max_utterance_duration_ms: int = 8000,
    trailing_silence_cap_ms: int = 350,
    keep_gap_as_field: bool = True,
) -> SubtitleModel:
    """
    从 asr_result.json 构建 Subtitle Model v1.3（SSOT）。

    核心理念：
    - ASR raw utterances 不是 SSOT（它们是模型导向的，不是视觉/听觉友好的）
    - 真正的 SSOT 应该基于 word-level timestamps + silence 重建
    - 使用 Utterance Normalization 重建视觉友好的 utterance 边界

    Args:
        raw_response: ASR 原始响应
        source_lang: 源语言代码（如 "zh", "en"），默认 "zh"
        audio_duration_ms: 音频时长（毫秒，可选）
        max_chars: cue 最大字数阈值（用于语义切分）
        max_dur_ms: cue 最大时长阈值（毫秒，用于语义切分）
        hard_punc: 硬标点（必切）
        soft_punc: 软标点（可切）

        # Utterance Normalization 配置：
        silence_split_threshold_ms: 静音切分阈值（ms），超过则切分 utterance
        min_utterance_duration_ms: 最小 utterance 时长（ms）
        max_utterance_duration_ms: 最大 utterance 时长（ms）
        trailing_silence_cap_ms: 尾部静音上限（ms）
        keep_gap_as_field: 是否保留 gap 为独立字段

    Returns:
        SubtitleModel: 完整的字幕模型 v1.3（SSOT）
    """
    # 1. 从 ASR response 中提取所有 word-level timestamps + speaker→gender 映射
    all_words, speaker_gender_map = extract_all_words_from_raw_response(raw_response)

    if not all_words:
        return SubtitleModel(
            schema=SchemaInfo(name="subtitle.model", version="1.3"),
            audio={"duration_ms": audio_duration_ms} if audio_duration_ms else None,
            utterances=[],
        )

    # 2. 使用 Utterance Normalization 重建 utterance 边界
    #    speaker 变化是硬边界，gender 从 speaker_gender_map 继承
    norm_config = NormalizationConfig(
        silence_split_threshold_ms=silence_split_threshold_ms,
        min_utterance_duration_ms=min_utterance_duration_ms,
        max_utterance_duration_ms=max_utterance_duration_ms,
        trailing_silence_cap_ms=trailing_silence_cap_ms,
        keep_gap_as_field=keep_gap_as_field,
    )
    normalized_utts = normalize_utterances(all_words, norm_config, speaker_gender_map)

    # 3. 将 NormalizedUtterance 转换为 SubtitleUtterance
    utterances: List[SubtitleUtterance] = []

    for idx, norm_utt in enumerate(normalized_utts, start=1):
        utt_text = norm_utt.text

        if not utt_text:
            continue

        # 规范化 speaker ID
        normalized_speaker = normalize_speaker_id(norm_utt.speaker)

        # 提取 emotion 元数据
        metadata = extract_utterance_metadata(raw_response, norm_utt)

        utterance_emotion: Optional[EmotionInfo] = None
        if metadata.get("emotion"):
            utterance_emotion = build_emotion_info(
                emotion_label=metadata.get("emotion"),
                emotion_score=metadata.get("emotion_score"),
                emotion_degree=metadata.get("emotion_degree"),
            )

        # 计算语速（始终用 word 级时间轴，它是真实声学测量值）
        zh_tps = calculate_speech_rate_zh_tps(norm_utt.words)

        # 按照语义切分 cues
        cue_data_list = semantic_split_text(
            text=utt_text,
            words=norm_utt.words,
            max_chars=max_chars,
            max_dur_ms=max_dur_ms,
            hard_punc=hard_punc,
            soft_punc=soft_punc,
        )

        cues: List[SubtitleCue] = []
        for cue_text, cue_start_ms, cue_end_ms in cue_data_list:
            cues.append(
                SubtitleCue(
                    start_ms=int(cue_start_ms),
                    end_ms=int(cue_end_ms),
                    source=SourceText(lang=source_lang, text=str(cue_text)),
                )
            )

        if not cues:
            continue

        # utterance 时间范围使用 normalized 的边界（已经是 SSOT）
        utterances.append(
            SubtitleUtterance(
                utt_id=f"utt_{idx:04d}",
                speaker=SpeakerInfo(
                    id=normalized_speaker,
                    gender=norm_utt.gender or None,
                    speech_rate=SpeechRate(zh_tps=float(zh_tps)),
                    emotion=utterance_emotion,
                ),
                start_ms=norm_utt.start_ms,
                end_ms=norm_utt.end_ms,
                cues=cues,
            )
        )

    # 4. 构建 audio 元数据
    audio: Optional[Dict[str, Any]] = None
    if audio_duration_ms is not None:
        audio = {"duration_ms": audio_duration_ms}
    elif utterances:
        # 从最后一个 utterance 推断时长
        last_utt = utterances[-1]
        audio = {"duration_ms": last_utt.end_ms}

    # 5. 构建 Subtitle Model v1.3
    model = SubtitleModel(
        schema=SchemaInfo(name="subtitle.model", version="1.3"),
        audio=audio,
        utterances=utterances,
    )

    return model



