"""
ASR 后处理：ASR raw → Subtitle Model (SSOT)

职责：
- 从 ASR utterances 生成 Subtitle Model (Segment[])
- 这是系统唯一可以生成 Subtitle Model 的模块
- Subtitle Model 是系统的唯一事实源（SSOT）

核心处理：
- utterance → segment 转换（时间轴、文本、speaker）
- emotion 决策（score < 阈值 → neutral）
- speaker 规范化（"1" → "spk_1"）
- 文本清洗（去重标点、合并碎句）
- 语义切分（长句兜底）

不负责：
- ❌ ASR 识别（由 models/doubao 负责）
- ❌ SRT 格式化（由 render_srt.py 负责）
- ❌ VTT 格式化（由 render_vtt.py 负责）
- ❌ 任何文件 IO
- ❌ 任何格式渲染

架构原则：
- asr_post 不产"文件"，只产"模型"
- Subtitle Model 是 SSOT，任何字幕文件均为其派生视图
- 下游模块（render_srt.py）负责格式渲染

轴优先（口型优先）切分策略：宁碎不拖、宁切不断

Subtitle Axis Mode:
- NO MERGE (never merge segments)
- Time-axis first: cut on any perceptible pause
- Split only: by speaker, by utterance (VAD), by word-gap, and by semantic/length constraints
"""
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from .profiles import POSTPROFILES
from pikppo.schema import Utterance, Segment, Word
from pikppo.utils.text import normalize_text


# 标点符号集合（用于匹配时忽略）
_PUNC = set("，。！？、；：,.!?;:\"'（）()【】[]《》<>…—- ")


def _plain_with_map(s: str):
    """
    返回 plain 字符串（去掉标点/空白），以及 plain_idx -> orig_idx 映射。
    
    用于在匹配时忽略标点/空白，然后映射回原文索引以保留标点。
    
    Args:
        s: 原始字符串
    
    Returns:
        (plain_string, plain_to_orig_mapping)
        - plain_string: 去掉标点/空白后的纯文本
        - plain_to_orig_mapping: 列表，plain 索引 -> 原文索引的映射
    """
    plain = []
    p2o = []
    for i, ch in enumerate(s):
        # 去掉空白和标点（用于匹配）
        if ch in _PUNC or ch.isspace():
            continue
        ch2 = unicodedata.normalize("NFKC", ch)
        if not ch2:
            continue
        # NFKC 极少数情况可能变多字符，这里取第一个即可
        plain.append(ch2[0])
        p2o.append(i)
    return "".join(plain), p2o


def words_to_segment(
    words: List[Word], 
    speaker: str, 
    full_text: Optional[str] = None,
    emotion: Optional[str] = None,
    gender: Optional[str] = None,
) -> Segment:
    """
    将 words 列表转换为 Segment。
    
    核心逻辑：使用"忽略标点/空白匹配"来定位 words 在 full_text 中的位置，
    然后切片原文 full_text 以保留中间标点。
    
    Args:
        words: 词列表
        speaker: 说话人标识
        full_text: 完整的 utterance 文本（如果提供，优先使用以保留标点符号）
        emotion: 情绪标签（可选）
        gender: 性别标签（可选）
    
    Returns:
        Segment 对象（保留原始标点，不在此处 strip）
    """
    if not words:
        raise ValueError("words 列表不能为空")
    
    start_ms = words[0].start_ms
    end_ms = words[-1].end_ms
    
    # 如果提供了 full_text，使用"忽略标点匹配"来定位并切片原文
    if full_text:
        words_text = "".join(w.text for w in words)
        
        # 用"去标点/空白"的 plain 来匹配
        plain_full, p2o_full = _plain_with_map(full_text)
        plain_words, _ = _plain_with_map(words_text)
        
        pos = plain_full.find(plain_words)
        if pos >= 0:
            # 找到匹配位置，映射回原文索引
            orig_start = p2o_full[pos]
            orig_end = p2o_full[pos + len(plain_words) - 1] + 1
            # ✅ 最终切片必须用原文 full_text（保留中间标点）
            text = full_text[orig_start:orig_end]
        else:
            # 找不到就别用 words 拼接（那必丢标点）
            # 兜底用 full_text（至少保标点）
            text = full_text
    else:
        # 没有提供 full_text，使用 words 拼接（无法保留标点）
        text = normalize_text("".join(w.text for w in words))
    
    # 重要：不在此处 strip 标点！
    # 标点清理应该在最终 segment 合并完成后统一处理（在 subtitles.py 生成 SRT 时）
    
    return Segment(
        speaker=speaker,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        emotion=emotion,
        gender=gender,
    )


def split_utterance_axis_first(
    utt: Utterance,
    soft_gap_ms: int = 400,
) -> List[Segment]:
    """
    Step 2：对每个 utterance 内部按 speaker + word-gap 再切（轴优先）
    
    轴优先：gap ≥ soft_gap_ms 就切（宁碎不拖）
    """
    words = utt.words or []
    
    # 如果没有 words，直接返回 utterance 级别的 segment
    if not words:
        return [Segment(
            speaker=utt.speaker,
            start_ms=utt.start_ms,
            end_ms=utt.end_ms,
            text=normalize_text(utt.text),
            emotion=utt.emotion,
            gender=utt.gender,
        )]
    
    segments: List[Segment] = []
    cur_words: List[Word] = [words[0]]
    cur_spk = getattr(words[0], "speaker", "") or utt.speaker
    
    for w in words[1:]:
        w_spk = getattr(w, "speaker", "") or utt.speaker
        gap = w.start_ms - cur_words[-1].end_ms
        
        # 1) speaker 变化：必切（硬边界）
        if w_spk != cur_spk:
            segments.append(words_to_segment(
                cur_words, cur_spk, full_text=utt.text,
                emotion=utt.emotion, gender=utt.gender
            ))
            cur_words = [w]
            cur_spk = w_spk
            continue
        
        # 2) 停顿切：轴优先（soft_gap_ms 也切）
        if gap >= soft_gap_ms:
            segments.append(words_to_segment(
                cur_words, cur_spk, full_text=utt.text,
                emotion=utt.emotion, gender=utt.gender
            ))
            cur_words = [w]
            continue
        
        # 否则累积到当前 segment
        cur_words.append(w)
    
    # 添加最后一个 segment
    if cur_words:
        segments.append(words_to_segment(
            cur_words, cur_spk, full_text=utt.text,
            emotion=utt.emotion, gender=utt.gender
        ))
    
    return segments


def semantic_split_long_segment(
    seg: Segment,
    words: List[Word],
    max_chars: int = 18,
    max_dur_ms: int = 2800,
    hard_punc: str = "。！？；",
    soft_punc: str = "，",
) -> List[Segment]:
    """
    Step 3：段内"长句兜底语义切"（只在超长触发）
    
    如果 duration > max_dur_ms 或 len(text) > max_chars，按标点拆
    """
    # 检查是否需要切分
    need_split = False
    if seg.end_ms - seg.start_ms > max_dur_ms:
        need_split = True
    elif len(seg.text) > max_chars:
        need_split = True
    
    if not need_split:
        return [seg]
    
    # 需要切分：按标点优先拆
    text = seg.text
    segments: List[Segment] = []
    text_pos = 0
    word_idx = 0
    
    # 找到 words 中对应此 segment 的范围
    seg_words: List[Word] = []
    for w in words:
        if w.start_ms >= seg.start_ms and w.end_ms <= seg.end_ms:
            seg_words.append(w)
    
    if not seg_words:
        # 如果没有 words，按字符比例分配时间
        return _split_by_chars_proportional(seg, max_chars, hard_punc, soft_punc)
    
    while text_pos < len(text) and word_idx < len(seg_words):
        remaining = text[text_pos:]
        
        # 优先在硬标点处切
        hard_cut = -1
        for p in hard_punc:
            idx = remaining.find(p, 0, max_chars + 1)
            if idx > 0 and (hard_cut < 0 or idx < hard_cut):
                hard_cut = idx
        
        if hard_cut > 0:
            seg_text = remaining[:hard_cut + 1]
            seg_start, seg_end = _allocate_timestamps_from_words(
                seg_text, seg_words, word_idx, seg.start_ms, seg.end_ms
            )
            segments.append(Segment(
                speaker=seg.speaker,
                start_ms=seg_start,
                end_ms=seg_end,
                text=seg_text,
                emotion=seg.emotion,
                gender=seg.gender,
            ))
            text_pos += hard_cut + 1
            word_idx = _advance_word_idx(seg_text, seg_words, word_idx)
            continue
        
        # 如果超过 max_chars，找软标点
        if len(remaining) > max_chars:
            soft_cut = -1
            for p in soft_punc:
                idx = remaining.rfind(p, 0, max_chars + 1)
                if idx > 0 and (soft_cut < 0 or idx > soft_cut):
                    soft_cut = idx
            
            if soft_cut > 0:
                seg_text = remaining[:soft_cut + 1]
                seg_start, seg_end = _allocate_timestamps_from_words(
                    seg_text, seg_words, word_idx, seg.start_ms, seg.end_ms
                )
                segments.append(Segment(
                    speaker=seg.speaker,
                    start_ms=seg_start,
                    end_ms=seg_end,
                    text=seg_text,
                ))
                text_pos += soft_cut + 1
                word_idx = _advance_word_idx(seg_text, seg_words, word_idx)
                continue
            
            # 没有标点，按字数硬切
            seg_text = remaining[:max_chars]
            seg_start, seg_end = _allocate_timestamps_from_words(
                seg_text, seg_words, word_idx, seg.start_ms, seg.end_ms
            )
            segments.append(Segment(
                speaker=seg.speaker,
                start_ms=seg_start,
                end_ms=seg_end,
                text=seg_text,
                emotion=seg.emotion,
                gender=seg.gender,
            ))
            text_pos += max_chars
            word_idx = _advance_word_idx(seg_text, seg_words, word_idx)
        else:
            # 剩余部分不足 max_chars，直接输出
            seg_text = remaining
            seg_start, seg_end = _allocate_timestamps_from_words(
                seg_text, seg_words, word_idx, seg.start_ms, seg.end_ms
            )
            segments.append(Segment(
                speaker=seg.speaker,
                start_ms=seg_start,
                end_ms=seg_end,
                text=seg_text,
                emotion=seg.emotion,
                gender=seg.gender,
            ))
            break
    
    return segments if segments else [seg]


def _split_by_chars_proportional(
    seg: Segment,
    max_chars: int,
    hard_punc: str,
    soft_punc: str,
) -> List[Segment]:
    """当没有 words 时，按字符比例分配时间切分"""
    text = seg.text
    if len(text) <= max_chars:
        return [seg]
    
    segments: List[Segment] = []
    dur = seg.end_ms - seg.start_ms
    
    text_pos = 0
    while text_pos < len(text):
        remaining = text[text_pos:]
        
        # 优先在硬标点处切
        hard_cut = -1
        for p in hard_punc:
            idx = remaining.find(p, 0, max_chars + 1)
            if idx > 0 and (hard_cut < 0 or idx < hard_cut):
                hard_cut = idx
        
        if hard_cut > 0:
            seg_text = remaining[:hard_cut + 1]
            seg_start = seg.start_ms + int(dur * (text_pos / len(text)))
            seg_end = seg.start_ms + int(dur * ((text_pos + len(seg_text)) / len(text)))
            segments.append(Segment(
                speaker=seg.speaker,
                start_ms=seg_start,
                end_ms=seg_end,
                text=seg_text,
                emotion=seg.emotion,
                gender=seg.gender,
            ))
            text_pos += hard_cut + 1
            continue
        
        # 如果超过 max_chars，找软标点
        if len(remaining) > max_chars:
            soft_cut = -1
            for p in soft_punc:
                idx = remaining.rfind(p, 0, max_chars + 1)
                if idx > 0 and (soft_cut < 0 or idx > soft_cut):
                    soft_cut = idx
            
            if soft_cut > 0:
                seg_text = remaining[:soft_cut + 1]
                seg_start = seg.start_ms + int(dur * (text_pos / len(text)))
                seg_end = seg.start_ms + int(dur * ((text_pos + len(seg_text)) / len(text)))
                segments.append(Segment(
                    speaker=seg.speaker,
                    start_ms=seg_start,
                    end_ms=seg_end,
                    text=seg_text,
                ))
                text_pos += soft_cut + 1
                continue
            
            # 没有标点，按字数硬切
            seg_text = remaining[:max_chars]
            seg_start = seg.start_ms + int(dur * (text_pos / len(text)))
            seg_end = seg.start_ms + int(dur * ((text_pos + len(seg_text)) / len(text)))
            segments.append(Segment(
                speaker=seg.speaker,
                start_ms=seg_start,
                end_ms=seg_end,
                text=seg_text,
                emotion=seg.emotion,
                gender=seg.gender,
            ))
            text_pos += max_chars
        else:
            # 剩余部分
            seg_text = remaining
            seg_start = seg.start_ms + int(dur * (text_pos / len(text)))
            seg_end = seg.end_ms
            segments.append(Segment(
                speaker=seg.speaker,
                start_ms=seg_start,
                end_ms=seg_end,
                text=seg_text,
                emotion=seg.emotion,
                gender=seg.gender,
            ))
            break
    
    return segments if segments else [seg]


def _allocate_timestamps_from_words(
    seg_text: str,
    words: List[Word],
    word_idx: int,
    chunk_start: int,
    chunk_end: int,
) -> Tuple[int, int]:
    """为语义切分的文本片段分配时间戳（使用 words 边界）"""
    if not words or word_idx >= len(words):
        return (chunk_start, chunk_end)
    
    # 找到 seg_text 对应的 words 范围
    text_consumed = 0
    start_word_idx = word_idx
    end_word_idx = word_idx
    
    for i in range(word_idx, len(words)):
        w = words[i]
        w_text = w.text
        if text_consumed + len(w_text) <= len(seg_text):
            text_consumed += len(w_text)
            end_word_idx = i + 1
        else:
            break
    
    if start_word_idx < len(words) and end_word_idx > start_word_idx:
        seg_start = words[start_word_idx].start_ms
        seg_end = words[end_word_idx - 1].end_ms
        return (seg_start, seg_end)
    
    # 回退：按字符比例分配时间
    if word_idx < len(words):
        ratio = len(seg_text) / max(1, sum(len(w.text) for w in words[word_idx:]))
        dur = chunk_end - chunk_start
        seg_start = chunk_start
        seg_end = chunk_start + int(dur * ratio)
        return (seg_start, seg_end)
    
    return (chunk_start, chunk_end)


def _advance_word_idx(seg_text: str, words: List[Word], word_idx: int) -> int:
    """根据消耗的文本，推进 word_idx"""
    text_consumed = 0
    for i in range(word_idx, len(words)):
        w = words[i]
        text_consumed += len(w.text)
        if text_consumed >= len(seg_text):
            return i + 1
    return len(words)


def clean_segment_text(text: str) -> str:
    """
    清理最终 segment 文本的首尾标点（只在最终合并后调用）。
    
    规则：
    - 行首：可以大胆清（，。！？、；：）】」）
    - 行尾：要保守（只清，、；：，保留。！？）
    
    Never strip punctuation before final segment merge.
    Punctuation cleanup is a presentation concern, not a segmentation concern.
    
    Args:
        text: 最终 segment 的文本
    
    Returns:
        清理后的文本
    """
    # 行首标点（可以大胆清）
    leading_puncs = "，。！？、；：）】」"
    # 行尾标点（保守：只清连接性标点，保留句号问号感叹号）
    trailing_puncs = "，、；："
    
    # 去掉行首标点
    text = text.lstrip(leading_puncs)
    # 去掉行尾连接性标点（但保留句号、问号、感叹号）
    text = text.rstrip(trailing_puncs)
    
    return text


def speaker_aware_postprocess(
    utterances: List[Utterance],
    profile_name: str = "axis",  # 默认：轴优先模式
    profiles: Dict[str, Dict[str, Any]] = None,
) -> List[Segment]:
    """
    轴优先（口型优先）切分策略：Utterance[] → Segment[]
    
    总原则：
    1. 时间轴优先于语义：只要有可感知停顿，就切
    2. 只拆不合：任何情况下都不把两段合回去
    3. speaker 永远是硬边界：不同 speaker 必切
    4. VAD 是主切，word-gap 是兜底切
    
    切分信号优先级（从高到低）：
    A. 硬切（必须切）：
       1. speaker 变化
       2. utterance 边界（VAD 输出）
       3. word-gap ≥ hard_gap_ms
       4. segment 时长 ≥ max_dur_ms
    
    B. 软切（轴切）：
       5. word-gap ≥ soft_gap_ms（400ms）
    
    C. 语义切（只用于"过长"兜底）：
       6. 文本超过 max_chars：按标点优先拆
    
    Args:
        utterances: 原始话语单元列表（已按时间排序）
        profile_name: 后处理策略名称（axis, axis_default, axis_soft）
        profiles: 后处理策略配置字典（如果为 None，使用默认配置）
    
    Returns:
        Segment[]: 已完成切分的中间数据结构（仍带 speaker，绝不合并）
    """
    if not utterances:
        return []
    
    # 获取后处理策略配置
    if profiles is None:
        profiles = POSTPROFILES
    
    # 获取配置（如果不存在，使用默认 "axis"）
    p = profiles.get(profile_name, POSTPROFILES.get("axis", {}))
    
    # 工程约束 1：axis* profiles 强制不合并（运行时 assert）
    if profile_name.startswith("axis"):
        allow_merge = p.get("allow_merge", False)
        assert not allow_merge, f"Axis profiles must never merge. Profile '{profile_name}' has allow_merge=True, which is forbidden."
    
    soft_gap_ms = int(p.get("soft_gap_ms", 400))  # 轴切：400ms 就切
    max_dur_ms = int(p.get("max_dur_ms", 2800))    # 单条字幕最大时长
    max_chars = int(p.get("max_chars", 18))         # 最大字数阈值
    hard_punc = p.get("hard_punc", "。！？；")      # 必切标点
    soft_punc = p.get("soft_punc", "，")            # 可切标点
    pad_end_ms = int(p.get("pad_end_ms", 60))       # 轻微拉长尾巴
    
    segments: List[Segment] = []
    
    # Step 1：先按 utterance（VAD）生成初始 segments（必切）
    # 轴优先：不跨 utterance 是铁律之一
    for utt in utterances:
        # Step 2：对每个 utterance 内部按 speaker + word-gap 再切
        utt_segments = split_utterance_axis_first(utt, soft_gap_ms)
        
        # Step 3：对每个 segment 检查是否需要语义切分（长句兜底）
        for seg in utt_segments:
            # 检查硬边界：max_dur_ms（如果超过，强制切分）
            if seg.end_ms - seg.start_ms > max_dur_ms:
                # 超过最大时长，强制语义切分
                long_segs = semantic_split_long_segment(
                    seg, utt.words or [], max_chars, max_dur_ms, hard_punc, soft_punc
                )
                segments.extend(long_segs)
            else:
                # 检查是否需要语义切分（超长文本）
                if len(seg.text) > max_chars:
                    long_segs = semantic_split_long_segment(
                        seg, utt.words or [], max_chars, max_dur_ms, hard_punc, soft_punc
                    )
                    segments.extend(long_segs)
                else:
                    # 正常 segment，应用 pad_end_ms
                    segments.append(Segment(
                        speaker=seg.speaker,
                        start_ms=seg.start_ms,
                        end_ms=seg.end_ms + pad_end_ms,
                        text=seg.text,
                        emotion=seg.emotion,
                        gender=seg.gender,
                    ))
    
    # Step 4：禁止合并（明确写死）
    # IMPORTANT: NO MERGE. Keep original segmentation.
    # 无论多短的段，都保持原状
    
    # 安全检查：确保 segments 时间严格递增
    fixed: List[Segment] = []
    last_end = -1
    for seg in segments:
        st, et = seg.start_ms, seg.end_ms
        if st <= last_end:
            st = last_end + 1
        if et <= st:
            et = st + 200
        fixed.append(Segment(
            speaker=seg.speaker,
            start_ms=st,
            end_ms=et,
            text=seg.text,
            emotion=seg.emotion,
            gender=seg.gender,
        ))
        last_end = et
    
    # Step 5：最终文本清理（清理首尾标点）
    # 在最终返回前统一清理所有 segments 的首尾标点
    cleaned: List[Segment] = []
    for seg in fixed:
        cleaned_text = clean_segment_text(seg.text)
        cleaned.append(Segment(
            speaker=seg.speaker,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            text=cleaned_text,
            emotion=seg.emotion,
            gender=seg.gender,
        ))
    
    return cleaned
