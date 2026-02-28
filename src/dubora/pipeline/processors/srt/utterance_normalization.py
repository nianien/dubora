"""
Utterance Normalization: 基于 speech + silence 重建视觉友好的 utterance 边界

核心理念：
- ASR raw utterances 不是 SSOT（它们是模型导向的，不是视觉/听觉友好的）
- 真正的 SSOT 应该基于"说话段 + 停顿"重建
- utterance 边界以 speech segment 为主，pause/silence 是一等公民

输入：word-level timestamps（从 ASR）
输出：Visual Utterances（真正的 SSOT）

关键参数（可在 config/settings.py 配置）：
- silence_split_threshold_ms: 切分阈值，超过则切分 utterance
- min_utterance_duration_ms: 最小 utterance 时长
- max_utterance_duration_ms: 最大 utterance 时长
- trailing_silence_cap_ms: 尾部静音上限
- keep_gap_as_field: 是否保留 gap 为独立字段
"""
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from dubora.schema.types import Word


@dataclass
class NormalizedUtterance:
    """
    规范化后的 utterance（Visual Utterance）。

    这是真正的 SSOT 单元，基于 speech + silence 重建。
    """
    start_ms: int              # 发声开始时间
    end_ms: int                # 发声结束时间（不含 trailing silence）
    words: List[Word]          # 属于此 utterance 的 words
    speaker: str               # 说话人 ID
    gender: str = ""           # 性别（从 raw utterance additions 继承）
    gap_after_ms: int = 0      # 此 utterance 后的静音时长（如果 keep_gap_as_field=True）

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def text(self) -> str:
        """合并所有 words 的文本"""
        return "".join(w.text for w in self.words)


@dataclass
class NormalizationConfig:
    """Utterance Normalization 配置"""
    silence_split_threshold_ms: int = 450   # 切分阈值
    min_utterance_duration_ms: int = 900    # 最小时长
    max_utterance_duration_ms: int = 8000   # 最大时长
    trailing_silence_cap_ms: int = 350      # 尾部静音上限
    keep_gap_as_field: bool = True          # 保留 gap 为独立字段


def normalize_utterances(
    all_words: List[Word],
    config: Optional[NormalizationConfig] = None,
    speaker_gender_map: Optional[Dict[str, str]] = None,
) -> List[NormalizedUtterance]:
    """
    基于 word-level timestamps 重建 utterance 边界。

    算法：
    1. 计算所有 word 之间的 gaps（speaker 变化是硬边界）
    2. 根据 silence_split_threshold_ms 切分
    3. 应用 min/max duration 约束
    4. 处理 trailing silence
    5. 记录 gap_after_ms（如果 keep_gap_as_field=True）

    Args:
        all_words: 所有 words（按时间排序）
        config: 配置参数
        speaker_gender_map: speaker→gender 映射（从 raw response 提取）

    Returns:
        List[NormalizedUtterance]: 规范化后的 utterances
    """
    if config is None:
        config = NormalizationConfig()

    if not all_words:
        return []

    # 确保 words 按时间排序
    sorted_words = sorted(all_words, key=lambda w: (w.start_ms, w.end_ms))

    # Step 1: 初步切分 - 基于 silence threshold
    raw_chunks = _split_by_silence(sorted_words, config.silence_split_threshold_ms)

    # Step 2: 应用 min duration 约束 - 合并过短的 chunks
    merged_chunks = _merge_short_chunks(raw_chunks, config.min_utterance_duration_ms)

    # Step 3: 应用 max duration 约束 - 切分过长的 chunks
    final_chunks = _split_long_chunks(
        merged_chunks,
        config.max_utterance_duration_ms,
        config.silence_split_threshold_ms // 2,  # 使用更小的阈值进行二次切分
    )

    # Step 4: 构建 NormalizedUtterance
    utterances = _build_utterances(
        final_chunks,
        sorted_words,
        config.trailing_silence_cap_ms,
        config.keep_gap_as_field,
        speaker_gender_map=speaker_gender_map,
    )

    return utterances


def _split_by_silence(
    words: List[Word],
    threshold_ms: int,
) -> List[List[Word]]:
    """
    根据静音阈值切分 words 为多个 chunks。

    Args:
        words: 按时间排序的 words
        threshold_ms: 静音切分阈值

    Returns:
        List[List[Word]]: chunks 列表
    """
    if not words:
        return []

    chunks: List[List[Word]] = []
    current_chunk: List[Word] = [words[0]]

    for i in range(1, len(words)):
        prev_word = words[i - 1]
        curr_word = words[i]

        # 计算两个 word 之间的 gap
        gap_ms = curr_word.start_ms - prev_word.end_ms

        # speaker 变化是硬边界，必切（不同说话人不能混在同一个 chunk）
        speaker_changed = (
            curr_word.speaker and prev_word.speaker
            and curr_word.speaker != prev_word.speaker
        )

        if gap_ms >= threshold_ms or speaker_changed:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [curr_word]
        else:
            current_chunk.append(curr_word)

    # 添加最后一个 chunk
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _gap_between_chunks(prev_chunk: List[Word], next_chunk: List[Word]) -> int:
    """计算两个 chunk 之间的 gap（毫秒）"""
    if not prev_chunk or not next_chunk:
        return 0
    return next_chunk[0].start_ms - prev_chunk[-1].end_ms


def _chunk_speaker(chunk: List[Word]) -> str:
    """取 chunk 的主 speaker（第一个有 speaker 的 word）"""
    for w in chunk:
        if w.speaker:
            return w.speaker
    return ""


def _can_merge(prev_chunk: List[Word], next_chunk: List[Word], max_merge_gap_ms: int) -> bool:
    """判断两个 chunk 是否可以合并：同 speaker + gap 在阈值内"""
    if not prev_chunk or not next_chunk:
        return False
    if _chunk_speaker(prev_chunk) != _chunk_speaker(next_chunk):
        return False
    return _gap_between_chunks(prev_chunk, next_chunk) <= max_merge_gap_ms


def _merge_short_chunks(
    chunks: List[List[Word]],
    min_duration_ms: int,
    max_merge_gap_ms: int = 1000,
) -> List[List[Word]]:
    """
    合并过短的 chunks。

    三个条件缺一不可：
    1. duration < min_duration_ms（短句才需要合并）
    2. gap <= max_merge_gap_ms（不跨大停顿）
    3. 同 speaker（不跨说话人）

    Args:
        chunks: 原始 chunks
        min_duration_ms: 最小时长
        max_merge_gap_ms: 合并允许的最大 gap（超过则保留为独立 chunk）

    Returns:
        List[List[Word]]: 合并后的 chunks
    """
    if not chunks:
        return []

    result: List[List[Word]] = []

    for chunk in chunks:
        chunk_duration = _get_chunk_duration(chunk)

        if chunk_duration >= min_duration_ms:
            result.append(chunk)
        elif result and _can_merge(result[-1], chunk, max_merge_gap_ms):
            result[-1].extend(chunk)
        else:
            result.append(chunk)

    # 处理最后一个可能过短的 chunk
    while len(result) > 1:
        last_duration = _get_chunk_duration(result[-1])
        if last_duration < min_duration_ms and _can_merge(result[-2], result[-1], max_merge_gap_ms):
            last_chunk = result.pop()
            result[-1].extend(last_chunk)
        else:
            break

    # 处理第一个可能过短的 chunk
    while len(result) > 1:
        first_duration = _get_chunk_duration(result[0])
        if first_duration < min_duration_ms and _can_merge(result[0], result[1], max_merge_gap_ms):
            first_chunk = result.pop(0)
            result[0] = first_chunk + result[0]
        else:
            break

    return result


def _split_long_chunks(
    chunks: List[List[Word]],
    max_duration_ms: int,
    secondary_threshold_ms: int,
) -> List[List[Word]]:
    """
    切分过长的 chunks。

    规则：
    - 如果一个 chunk 时长 > max_duration_ms，尝试在内部找合适的切分点
    - 使用更小的静音阈值进行二次切分
    - 如果无法切分，保持原样（避免强行切断连续语音）

    Args:
        chunks: 原始 chunks
        max_duration_ms: 最大时长
        secondary_threshold_ms: 二次切分的静音阈值

    Returns:
        List[List[Word]]: 切分后的 chunks
    """
    result: List[List[Word]] = []

    for chunk in chunks:
        chunk_duration = _get_chunk_duration(chunk)

        if chunk_duration <= max_duration_ms:
            result.append(chunk)
        else:
            # 尝试二次切分
            sub_chunks = _split_by_silence(chunk, secondary_threshold_ms)

            # 如果切分后仍有超长的，进一步处理
            for sub_chunk in sub_chunks:
                if _get_chunk_duration(sub_chunk) <= max_duration_ms:
                    result.append(sub_chunk)
                else:
                    # 找不到合适的切分点，按时长硬切
                    hard_split = _hard_split_chunk(sub_chunk, max_duration_ms)
                    result.extend(hard_split)

    return result


def _hard_split_chunk(
    chunk: List[Word],
    max_duration_ms: int,
) -> List[List[Word]]:
    """
    硬切分一个 chunk（最后手段）。

    尝试在接近 max_duration_ms 的位置找最大 gap 切分。
    """
    if not chunk:
        return []

    result: List[List[Word]] = []
    current: List[Word] = []
    current_start = chunk[0].start_ms

    for word in chunk:
        current.append(word)
        current_duration = word.end_ms - current_start

        if current_duration >= max_duration_ms and len(current) > 1:
            # 找到最佳切分点（最大 gap）
            best_split_idx = _find_best_split_point(current)

            if best_split_idx > 0:
                result.append(current[:best_split_idx])
                current = current[best_split_idx:]
                current_start = current[0].start_ms if current else word.end_ms

    if current:
        result.append(current)

    return result


def _find_best_split_point(words: List[Word]) -> int:
    """
    找到最佳切分点（gap 最大的位置）。
    """
    if len(words) <= 1:
        return 0

    max_gap = -1
    best_idx = len(words) // 2  # 默认中间

    for i in range(1, len(words)):
        gap = words[i].start_ms - words[i - 1].end_ms
        if gap > max_gap:
            max_gap = gap
            best_idx = i

    return best_idx


def _get_chunk_duration(chunk: List[Word]) -> int:
    """计算 chunk 的时长"""
    if not chunk:
        return 0
    return chunk[-1].end_ms - chunk[0].start_ms


def _build_utterances(
    chunks: List[List[Word]],
    all_words: List[Word],
    trailing_silence_cap_ms: int,
    keep_gap_as_field: bool,
    speaker_gender_map: Optional[Dict[str, str]] = None,
) -> List[NormalizedUtterance]:
    """
    构建 NormalizedUtterance 列表。

    Args:
        chunks: 切分后的 word chunks
        all_words: 所有 words（用于计算 gap）
        trailing_silence_cap_ms: 尾部静音上限
        keep_gap_as_field: 是否保留 gap 为独立字段
        speaker_gender_map: speaker→gender 映射（从 raw response 提取）

    Returns:
        List[NormalizedUtterance]: 规范化后的 utterances
    """
    if not chunks:
        return []

    gender_map = speaker_gender_map or {}
    utterances: List[NormalizedUtterance] = []

    for i, chunk in enumerate(chunks):
        if not chunk:
            continue

        # 确定 speaker（使用第一个有 speaker 的 word）
        speaker = ""
        for word in chunk:
            if word.speaker:
                speaker = word.speaker
                break

        # 计算 utterance 的时间范围
        start_ms = chunk[0].start_ms
        end_ms = chunk[-1].end_ms

        # 计算 gap_after_ms
        gap_after_ms = 0
        if i < len(chunks) - 1:
            next_chunk = chunks[i + 1]
            if next_chunk:
                gap_after_ms = next_chunk[0].start_ms - end_ms

        # 处理 trailing silence
        if not keep_gap_as_field:
            # 将 trailing silence（最多 cap）加入 end_ms
            actual_trailing = min(gap_after_ms, trailing_silence_cap_ms)
            end_ms += actual_trailing
            gap_after_ms = max(0, gap_after_ms - actual_trailing)

        utterances.append(
            NormalizedUtterance(
                start_ms=start_ms,
                end_ms=end_ms,
                words=chunk,
                speaker=speaker,
                gender=gender_map.get(speaker, ""),
                gap_after_ms=gap_after_ms,
            )
        )

    return utterances


def _attach_trailing_punctuation(
    utt_text: str,
    words_list: List[Dict[str, Any]],
) -> List[str]:
    """
    把 utterance 级别的标点附加到对应 word 的 text 后面。

    ASR 的 word 级别不含标点（如 "坐", "牢", "十", "年"），
    但 utterance text 有（如 "坐牢十年，我被冤枉杀父弑母的事，该去找个明白。"）。
    将尾部标点附加到对应 word：年 → 年，  事 → 事，  白 → 白。

    Args:
        utt_text: utterance 级别的完整文本（含标点）
        words_list: raw words 列表

    Returns:
        与 words_list 等长的文本列表（含尾部标点）
    """
    _PUNC_CHARS = set("，。！？、；：,.!?;:\"'（）()【】[]《》<>…—- ")

    # 提取有效 word texts
    w_texts = [str(w.get("text", "")).strip() for w in words_list]

    # 在 utt_text 中逐字匹配，找到每个 word 结束后的标点
    result = list(w_texts)  # 默认无标点
    utt_pos = 0

    for idx, wt in enumerate(w_texts):
        if not wt:
            continue
        # 跳过 utt_text 中当前位置的标点/空白（它们属于前一个 word，已经处理了）
        # 找到 wt 的第一个字符在 utt_text 中的位置
        found = False
        for scan in range(utt_pos, len(utt_text)):
            if utt_text[scan] == wt[0]:
                # 验证整个 word 匹配
                if utt_text[scan:scan + len(wt)] == wt:
                    utt_pos = scan + len(wt)
                    found = True
                    break
        if not found:
            continue

        # 收集尾部标点
        trailing = []
        while utt_pos < len(utt_text) and utt_text[utt_pos] in _PUNC_CHARS:
            trailing.append(utt_text[utt_pos])
            utt_pos += 1
        if trailing:
            result[idx] = wt + "".join(trailing)

    return result


def extract_all_words_from_raw_response(
    raw_response: Dict[str, Any],
) -> Tuple[List[Word], Dict[str, str]]:
    """
    从 ASR raw response 中提取所有 words 和 speaker→gender 映射。

    这是 Utterance Normalization 的输入准备步骤。
    完全忽略 ASR 的 utterance 边界，只提取 word-level timestamps。
    标点从 utterance 级别的 text 附加到对应 word 的 text 后面。

    Args:
        raw_response: ASR 原始响应

    Returns:
        (all_words, speaker_gender_map):
        - all_words: 所有 words（按时间排序，含尾部标点）
        - speaker_gender_map: speaker→gender 映射（如 {"1": "male"}）
    """
    result = raw_response.get("result") or {}
    raw_utterances = result.get("utterances") or []

    all_words: List[Word] = []
    speaker_gender_map: Dict[str, str] = {}

    for raw_utt in raw_utterances:
        # 从 utterance 获取 speaker 和 gender
        additions = raw_utt.get("additions") or {}
        default_speaker = str(additions.get("speaker", "0"))
        gender = additions.get("gender")
        if default_speaker and gender and default_speaker not in speaker_gender_map:
            speaker_gender_map[default_speaker] = str(gender).strip()

        # 提取 words
        words_list = raw_utt.get("words") or []
        if not words_list:
            continue

        # 把 utterance text 的标点附加到 word text
        utt_text = str(raw_utt.get("text", ""))
        enriched_texts = _attach_trailing_punctuation(utt_text, words_list)

        for i, w in enumerate(words_list):
            text = enriched_texts[i] if i < len(enriched_texts) else str(w.get("text", "")).strip()
            if not text:
                continue

            # word 级别的 speaker（如果存在）
            w_additions = w.get("additions") or {}
            w_speaker = str(w_additions.get("speaker", default_speaker))

            all_words.append(
                Word(
                    start_ms=int(w.get("start_time", 0)),
                    end_ms=int(w.get("end_time", w.get("start_time", 0))),
                    text=text,
                    speaker=w_speaker,
                )
            )

    # 按时间排序
    all_words.sort(key=lambda w: (w.start_ms, w.end_ms))

    return all_words, speaker_gender_map


def extract_utterance_metadata(
    raw_response: Dict[str, Any],
    normalized_utt: NormalizedUtterance,
) -> Dict[str, Any]:
    """
    为 normalized utterance 提取元数据（emotion, gender 等）。

    通过时间范围匹配，从 ASR raw response 中提取对应的元数据。

    Args:
        raw_response: ASR 原始响应
        normalized_utt: 规范化后的 utterance

    Returns:
        Dict 包含 emotion, gender 等字段
    """
    result = raw_response.get("result") or {}
    raw_utterances = result.get("utterances") or []

    metadata: Dict[str, Any] = {
        "emotion": None,
        "emotion_score": None,
        "emotion_degree": None,
        "gender": None,
    }

    # 找到时间范围重叠最大的 raw utterance
    best_overlap = 0
    best_raw_utt = None

    for raw_utt in raw_utterances:
        raw_start = int(raw_utt.get("start_time", 0))
        raw_end = int(raw_utt.get("end_time", raw_start))

        # 计算重叠
        overlap_start = max(normalized_utt.start_ms, raw_start)
        overlap_end = min(normalized_utt.end_ms, raw_end)
        overlap = max(0, overlap_end - overlap_start)

        if overlap > best_overlap:
            best_overlap = overlap
            best_raw_utt = raw_utt

    if best_raw_utt:
        additions = best_raw_utt.get("additions") or {}
        metadata["emotion"] = additions.get("emotion")
        metadata["emotion_score"] = additions.get("emotion_score")
        metadata["emotion_degree"] = additions.get("emotion_degree")
        metadata["gender"] = additions.get("gender")

    return metadata


def build_word_to_raw_utterance_index(
    raw_response: Dict[str, Any],
) -> Dict[Tuple[int, int], int]:
    """
    构建 (word.start_ms, word.end_ms) → raw utterance idx 的映射。

    用于归一化后追溯 normalized utterance 中的 word 来自哪个原始 utterance。
    """
    result = raw_response.get("result") or {}
    raw_utterances = result.get("utterances") or []

    mapping: Dict[Tuple[int, int], int] = {}
    for utt_idx, raw_utt in enumerate(raw_utterances):
        words_list = raw_utt.get("words") or []
        for w in words_list:
            key = (
                int(w.get("start_time", 0)),
                int(w.get("end_time", w.get("start_time", 0))),
            )
            mapping[key] = utt_idx

    return mapping
