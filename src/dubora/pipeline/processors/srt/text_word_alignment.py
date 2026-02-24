"""
Text-Word Alignment: 将校准后文本的字符映射到 word 级时间轴

核心场景：
- asr.fix.json 中人工校准了 utterance.text（增删字符）
- asr-result.json 中有原始 word 级时间轴
- 需要为校准后文本的每个字符分配时间戳

算法：
1. 对 corrected_text 和 word_text（word.text 拼接）做 LCS 字符对齐
2. 能对齐的字符 → 继承对应 word 的时间轴
3. 新增的字符 → 在相邻已对齐字符之间线性插值
4. 文本未改动时 → 完全退化为原有 word 时间直用逻辑
"""
from typing import List, Optional, Tuple

from dubora.schema.types import Word


def _compute_lcs_alignment(
    text_a: str,
    text_b: str,
) -> List[Tuple[int, int]]:
    """
    计算两个字符串的 LCS（最长公共子序列），返回对齐的 (idx_a, idx_b) 索引对。

    使用标准 DP 算法 + 回溯。
    """
    m, n = len(text_a), len(text_b)
    if m == 0 or n == 0:
        return []

    # DP table
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if text_a[i - 1] == text_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # Backtrack
    pairs: List[Tuple[int, int]] = []
    i, j = m, n
    while i > 0 and j > 0:
        if text_a[i - 1] == text_b[j - 1]:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1

    pairs.reverse()
    return pairs


def _char_index_to_word_index(char_idx: int, word_char_offsets: List[Tuple[int, int]]) -> int:
    """
    将字符在拼接文本中的位置映射到 word 索引。

    word_char_offsets: [(start_char, end_char), ...] 每个 word 在拼接文本中的字符范围
    """
    for wi, (start, end) in enumerate(word_char_offsets):
        if start <= char_idx < end:
            return wi
    return len(word_char_offsets) - 1


def align_corrected_text_to_words(
    corrected_text: str,
    words: List[Word],
) -> List[Tuple[int, int]]:
    """
    将校准后文本的每个字符映射到时间轴 (start_ms, end_ms)。

    Args:
        corrected_text: 人工校准后的文本
        words: 原始 word 列表（含时间轴）

    Returns:
        与 corrected_text 等长的 [(start_ms, end_ms), ...] 列表，
        每个元素对应 corrected_text 中该字符的时间区间。
    """
    if not corrected_text or not words:
        if words:
            return [(words[0].start_ms, words[-1].end_ms)] * max(len(corrected_text), 1)
        return [(0, 0)] * max(len(corrected_text), 1)

    # 拼接 word texts
    word_texts = [w.text for w in words]
    word_concat = "".join(word_texts)

    # 构建字符 → word 索引映射
    word_char_offsets: List[Tuple[int, int]] = []
    pos = 0
    for wt in word_texts:
        word_char_offsets.append((pos, pos + len(wt)))
        pos += len(wt)

    # 快速路径：文本完全一致，直接用 word 时间
    if corrected_text == word_concat:
        result: List[Tuple[int, int]] = []
        for ci, ch in enumerate(corrected_text):
            wi = _char_index_to_word_index(ci, word_char_offsets)
            result.append((words[wi].start_ms, words[wi].end_ms))
        return result

    # LCS 对齐
    lcs_pairs = _compute_lcs_alignment(corrected_text, word_concat)

    # 为 corrected_text 的每个字符分配时间
    # 先标记已对齐的字符
    char_times: List[Optional[Tuple[int, int]]] = [None] * len(corrected_text)

    for ci, wi_char in lcs_pairs:
        wi = _char_index_to_word_index(wi_char, word_char_offsets)
        char_times[ci] = (words[wi].start_ms, words[wi].end_ms)

    # 插值未对齐的字符
    _interpolate_gaps(char_times, words)

    # 确保没有 None（理论上不应该出现）
    fallback = (words[0].start_ms, words[-1].end_ms)
    return [t if t is not None else fallback for t in char_times]


def _interpolate_gaps(
    char_times: List[Optional[Tuple[int, int]]],
    words: List[Word],
) -> None:
    """
    对 char_times 中的 None 位置进行线性插值。

    策略：
    - 找到前后最近的已对齐字符
    - 在它们之间线性插值
    - 首尾无对齐字符时，使用最近的 word 边界
    """
    n = len(char_times)
    if n == 0:
        return

    utt_start = words[0].start_ms
    utt_end = words[-1].end_ms

    # 找到所有已对齐的位置
    anchors: List[Tuple[int, int, int]] = []  # (char_idx, start_ms, end_ms)
    for i, t in enumerate(char_times):
        if t is not None:
            anchors.append((i, t[0], t[1]))

    if not anchors:
        # 无对齐点：等比分配
        total_dur = utt_end - utt_start
        for i in range(n):
            s = utt_start + int(total_dur * i / n)
            e = utt_start + int(total_dur * (i + 1) / n)
            char_times[i] = (s, e)
        return

    # 处理首段（第一个 anchor 之前）
    first_anchor_idx, first_start, first_end = anchors[0]
    if first_anchor_idx > 0:
        seg_start = utt_start
        seg_end = first_start
        count = first_anchor_idx
        dur = seg_end - seg_start
        for i in range(count):
            s = seg_start + int(dur * i / count)
            e = seg_start + int(dur * (i + 1) / count)
            char_times[i] = (s, e)

    # 处理中间段（两个 anchor 之间）
    for ai in range(len(anchors) - 1):
        left_idx, _, left_end = anchors[ai]
        right_idx, right_start, _ = anchors[ai + 1]
        gap_count = right_idx - left_idx - 1
        if gap_count <= 0:
            continue
        seg_start = left_end
        seg_end = right_start
        dur = seg_end - seg_start
        for k in range(gap_count):
            ci = left_idx + 1 + k
            s = seg_start + int(dur * k / gap_count)
            e = seg_start + int(dur * (k + 1) / gap_count)
            char_times[ci] = (s, e)

    # 处理尾段（最后一个 anchor 之后）
    last_anchor_idx, _, last_end = anchors[-1]
    if last_anchor_idx < n - 1:
        seg_start = last_end
        seg_end = utt_end
        count = n - last_anchor_idx - 1
        dur = seg_end - seg_start
        for i in range(count):
            ci = last_anchor_idx + 1 + i
            s = seg_start + int(dur * i / count)
            e = seg_start + int(dur * (i + 1) / count)
            char_times[ci] = (s, e)
