"""
构建 Subtitle Model：从 Segment[] 生成真正的 Subtitle Model (SSOT) v1.2

职责：
- 将 asr_post.py 生成的 Segment[] 转换为 Subtitle Model v1.2
- 规范化 speaker ID（"1" → "spk_1"）
- 保留完整的 emotion 语义（不丢失 confidence/intensity）
- 计算语速（speech_rate.zh_tps）
- 构建 utterances 结构（包含 cues）

架构原则：
- 这是唯一可以构建 Subtitle Model 的地方（asr_post 阶段）
- Subtitle Model 是 SSOT，包含完整语义
- SSOT 只保存原始事实，不包含任何翻译或执行信息

各阶段职责（ownership 清晰）：
- asr_post：写 utterances、cues[*].source、start/end/speaker、emotion(可选)、speech_rate
- mt：不写 SSOT（翻译结果单独保存）
- tts：不写 SSOT（只读生成 tts_jobs）
"""
from typing import Any, Dict, List, Optional, Tuple

from pikppo.schema import Segment, Word
from pikppo.schema.subtitle_model import (
    SubtitleModel,
    SubtitleCue,
    SubtitleUtterance,
    EmotionInfo,
    SourceText,
    SpeechRate,
    SchemaInfo,
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


def calculate_speech_rate_zh_tps(words: List[Word]) -> float:
    """
    计算中文语速（zh_tps: tokens per second）。
    
    计算规则：
    - token 来源：ASR word / char timestamps
    - 丢弃：start_ms < 0 或 end_ms < 0，空白 token
    - 合并 token 时间区间（union）
    - zh_tps = 有效 token 数 / token 时间戳 union 后的发声秒数
    
    Args:
        words: Word 列表（来自 ASR raw response）
    
    Returns:
        zh_tps: 每秒 token 数（浮点数）
    """
    if not words:
        return 0.0
    
    # 过滤有效 token：丢弃 start_ms < 0 或 end_ms < 0，空白 token
    valid_words: List[Word] = []
    for w in words:
        if w.start_ms < 0 or w.end_ms < 0:
            continue
        if not w.text or not w.text.strip():
            continue
        valid_words.append(w)
    
    if not valid_words:
        return 0.0
    
    # 合并 token 时间区间（union）
    # 将时间区间按 start_ms 排序，然后合并重叠区间
    intervals: List[Tuple[int, int]] = []
    for w in valid_words:
        intervals.append((w.start_ms, w.end_ms))
    
    # 排序
    intervals.sort(key=lambda x: x[0])
    
    # 合并重叠区间
    merged: List[Tuple[int, int]] = []
    for start, end in intervals:
        if not merged:
            merged.append((start, end))
        else:
            last_start, last_end = merged[-1]
            if start <= last_end:
                # 重叠，合并
                merged[-1] = (last_start, max(last_end, end))
            else:
                # 不重叠，添加新区间
                merged.append((start, end))
    
    # 计算总发声时间（秒）
    total_duration_sec = 0.0
    for start, end in merged:
        duration_ms = end - start
        if duration_ms > 0:
            total_duration_sec += duration_ms / 1000.0
    
    if total_duration_sec <= 0:
        return 0.0
    
    # 计算 zh_tps
    token_count = len(valid_words)
    zh_tps = token_count / total_duration_sec
    
    return zh_tps


def build_subtitle_model(
    segments: List[Segment],
    raw_response: Dict[str, Any],
    source_lang: str = "zh",  # 默认源语言为中文
    audio_duration_ms: Optional[int] = None,
) -> SubtitleModel:
    """
    从 Segment[] 构建 Subtitle Model v1.2（SSOT）。
    
    Args:
        segments: 切分后的 segments（来自 asr_post.py）
        raw_response: ASR 原始响应（SSOT，用于提取完整的 emotion 信息和 words）
        source_lang: 源语言代码（如 "zh", "en"），默认 "zh"
        audio_duration_ms: 音频时长（毫秒，可选）
    
    Returns:
        SubtitleModel: 完整的字幕模型 v1.2（SSOT）
    
    注意：
    - 从 raw_response 中提取完整的 emotion 信息和 words（用于计算语速）
    - raw_response 是 SSOT，包含完整语义信息
    - 只填写 source，不包含 target（翻译信息不属于 SSOT）
    - 构建 utterances 结构，包含 speech_rate.zh_tps
    """
    # 1. 从 raw_response 中提取原始 utterances 和 words
    result = raw_response.get("result") or {}
    raw_utterances = result.get("utterances") or []
    
    # 构建原始 utterance 映射（用于匹配 segments 和计算语速）
    raw_utt_map: Dict[str, Dict[str, Any]] = {}
    for utt in raw_utterances:
        utt_start = int(utt.get("start_time", 0))
        utt_end = int(utt.get("end_time", utt_start))
        key = f"{utt_start}_{utt_end}"
        raw_utt_map[key] = utt
    
    # 2. 将 segments 按 utterance 分组
    # 首先需要将 segments 匹配到原始 utterances
    segments_by_utt: Dict[str, List[Segment]] = {}
    for seg in segments:
        # 找到包含此 segment 的原始 utterance
        matched_key = None
        for key, utt in raw_utt_map.items():
            utt_start = int(key.split("_")[0])
            utt_end = int(key.split("_")[1])
            # 检查 segment 是否在 utterance 时间范围内
            if seg.start_ms >= utt_start and seg.end_ms <= utt_end:
                matched_key = key
                break
        
        if matched_key:
            if matched_key not in segments_by_utt:
                segments_by_utt[matched_key] = []
            segments_by_utt[matched_key].append(seg)
        else:
            # 如果没有匹配到，创建一个新的 utterance key（使用 segment 的时间范围）
            key = f"{seg.start_ms}_{seg.end_ms}"
            if key not in segments_by_utt:
                segments_by_utt[key] = []
            segments_by_utt[key].append(seg)
    
    # 3. 构建 utterances 和 cues
    utterances: List[SubtitleUtterance] = []
    utt_index = 0
    
    for key, segs in segments_by_utt.items():
        if not segs:
            continue
        
        # 排序 segments（按时间）
        segs.sort(key=lambda x: (x.start_ms, x.end_ms))
        
        # 获取原始 utterance 信息（如果存在）
        raw_utt = raw_utt_map.get(key, {})
        utt_start = int(key.split("_")[0])
        utt_end = int(key.split("_")[1])
        
        # 确保 utterance 时间范围覆盖所有 segments
        if segs:
            utt_start = min(utt_start, segs[0].start_ms)
            utt_end = max(utt_end, segs[-1].end_ms)
        
        # 规范化 speaker ID（使用第一个 segment 的 speaker）
        normalized_speaker = normalize_speaker_id(segs[0].speaker)
        
        # 计算语速（从原始 utterance 的 words）
        zh_tps = 0.0
        if raw_utt:
            words_list = raw_utt.get("words") or []
            # 转换为 Word 对象
            words: List[Word] = []
            for w in words_list:
                words.append(Word(
                    start_ms=int(w.get("start_time", 0)),
                    end_ms=int(w.get("end_time", w.get("start_time", 0))),
                    text=str(w.get("text", "")).strip(),
                    speaker="",
                ))
            zh_tps = calculate_speech_rate_zh_tps(words)
        
        # 构建 cues
        cues: List[SubtitleCue] = []
        for i, seg in enumerate(segs):
            # 规范化 speaker ID
            seg_normalized_speaker = normalize_speaker_id(seg.speaker)
            
            # 尝试从 raw_response 中提取完整的 emotion 信息
            emotion_label = seg.emotion
            emotion_score = None
            emotion_degree = None
            
            # 从原始 utterance 的 additions 中提取 emotion 信息
            additions = raw_utt.get("additions") or {}
            if additions.get("emotion"):
                emotion_label = additions.get("emotion")
                emotion_score = additions.get("emotion_score")
                if emotion_score:
                    try:
                        emotion_score = float(emotion_score)
                    except (ValueError, TypeError):
                        emotion_score = None
                emotion_degree = additions.get("emotion_degree")
            
            # 构建 emotion info（可选）
            emotion_info: Optional[EmotionInfo] = None
            if emotion_label:
                emotion_info = build_emotion_info(
                    emotion_label=emotion_label,
                    emotion_score=emotion_score,
                    emotion_degree=emotion_degree,
                )
            
            # 生成 cue_id
            cue_id = f"cue_{len(cues) + 1:04d}"
            
            # 构建 source text（asr_post 阶段填写）
            source = SourceText(
                lang=source_lang,
                text=seg.text,
            )
            
            # v1.2: 不包含 target（翻译信息不属于 SSOT）
            cues.append(SubtitleCue(
                cue_id=cue_id,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                speaker=seg_normalized_speaker,
                source=source,
                emotion=emotion_info,  # 可选，用于 TTS style hint
            ))
        
        # 确保 utterance 时间范围正确
        if cues:
            utt_start = cues[0].start_ms
            utt_end = cues[-1].end_ms
        
        # 构建 utterance
        utt_id = f"utt_{utt_index + 1:04d}"
        utterances.append(SubtitleUtterance(
            utt_id=utt_id,
            speaker=normalized_speaker,
            start_ms=utt_start,
            end_ms=utt_end,
            speech_rate=SpeechRate(zh_tps=zh_tps),
            cues=cues,
        ))
        
        utt_index += 1
    
    # 4. 构建 audio 元数据
    audio: Optional[Dict[str, Any]] = None
    if audio_duration_ms is not None:
        audio = {
            "duration_ms": audio_duration_ms,
        }
    elif utterances:
        # 从最后一个 utterance 推断时长
        last_utt = utterances[-1]
        audio = {
            "duration_ms": last_utt.end_ms,
        }
    
    # 5. 构建 Subtitle Model v1.2
    model = SubtitleModel(
        schema=SchemaInfo(name="subtitle.model", version="1.2"),
        audio=audio,
        utterances=utterances,
    )
    
    return model
