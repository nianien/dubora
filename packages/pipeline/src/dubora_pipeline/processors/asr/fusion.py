"""ASR 三源融合处理器

Doubao 主干 + 腾讯时间轴填空 + Fish 文本替换。

公共 API:
- get_doubao_utterances(): 提取 Doubao 统一段
- get_tencent_segments(): 提取腾讯逗号级分段
- call_llm_diff(): LLM 比对主/副本文本差异
- fuse(): 三源融合核心
- fill_null_emotions(): emotion 回填
- extend_end_ms(): end_ms 延长
"""
import json
import re

from dubora_core.utils.logger import info, warning

_PUNC_RE = re.compile(r'[，。！？、；：""''（）…—\s,\.!?\-\[\]【】]')
_FISH_PUNC_RE = re.compile(r'[，。！？、；：""''（）…—\s,\.!?\-]')
_MIN_IN_GAP_MS = 300


# ── 文本工具 ─────────────────────────────────────────────────────────────

def strip_punct(s: str) -> str:
    return _PUNC_RE.sub('', s)


def lcs_ratio(a: str, b: str, use_min: bool = False) -> float:
    """最长公共子序列 / 串长度。use_min=True 用较短串，False 用较长串。"""
    a, b = strip_punct(a), strip_punct(b)
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n] / (min(m, n) if use_min else max(m, n))


# ── 提取各源数据 ─────────────────────────────────────────────────────────

def get_doubao_utterances(data: dict) -> list[dict]:
    """提取 Doubao utterances → 统一格式（含 words 供拆句用）。"""
    utts = []
    for u in data["result"]["utterances"]:
        adds = u.get("additions", {})
        utts.append({
            "start_ms": u["start_time"],
            "end_ms": u["end_time"],
            "text": u["text"],
            "speaker": adds.get("speaker", "?"),
            "emotion": adds.get("emotion"),
            "gender": adds.get("gender"),
            "source": "doubao",
            "words": u.get("words", []),
        })
    return sorted(utts, key=lambda x: x["start_ms"])


_SENTENCE_PUNCTS = set("。！？!?")
_COMMA_PUNCTS = set("，、；：,.;:")
_ALL_PUNCTS = _SENTENCE_PUNCTS | _COMMA_PUNCTS


def split_long_utterances(utts: list[dict], min_chars: int = 5, pause_ms: int = 800) -> list[dict]:
    """Utterance 内部按标点拆分。

    断句规则：
    1. 句号级标点（。！？）强制断，不管字数
    2. 逗号级标点（，、；：）满足 前面>=min_chars 字 OR 与下一个word间隔>pause_ms 才断

    逐字符对齐：非标点字符顺序匹配 word，标点归属前一个 word。
    原 utterance 边界不变，只在内部拆。
    """
    result = []
    for u in utts:
        words = u.get("words", [])
        text = u.get("text", "")
        if not words:
            result.append({k: v for k, v in u.items() if k != "words"})
            continue

        # 逐字符对齐：非标点 → word[wi]，标点 → None
        wi = 0
        items = []
        for ch in text:
            if ch in _ALL_PUNCTS:
                items.append((ch, None))
            elif wi < len(words):
                items.append((ch, wi))
                wi += 1
            else:
                items.append((ch, None))

        segments = []
        buf = []
        word_ids = []

        for ch, widx in items:
            buf.append(ch)
            if widx is not None:
                word_ids.append(widx)

            if ch not in _ALL_PUNCTS or not word_ids:
                continue

            should_split = False
            if ch in _SENTENCE_PUNCTS:
                # 句号级：强制断
                should_split = True
            else:
                # 逗号级：>= min_chars 或间隔 > pause_ms
                if len(buf) >= min_chars:
                    should_split = True
                else:
                    next_wi = word_ids[-1] + 1
                    if next_wi < len(words):
                        gap = words[next_wi]["start_time"] - words[word_ids[-1]]["end_time"]
                        if gap > pause_ms:
                            should_split = True

            if should_split:
                segments.append({
                    "start_ms": words[word_ids[0]]["start_time"],
                    "end_ms": words[word_ids[-1]]["end_time"],
                    "text": "".join(buf),
                    "speaker": u["speaker"],
                    "emotion": u.get("emotion"),
                    "gender": u.get("gender"),
                    "source": "doubao",
                })
                buf = []
                word_ids = []

        if buf and word_ids:
            segments.append({
                "start_ms": words[word_ids[0]]["start_time"],
                "end_ms": words[word_ids[-1]]["end_time"],
                "text": "".join(buf),
                "speaker": u["speaker"],
                "emotion": u.get("emotion"),
                "gender": u.get("gender"),
                "source": "doubao",
            })

        result.extend(segments if segments else [{k: v for k, v in u.items() if k != "words"}])

    if len(result) != len(utts):
        info(f"Split utterances: {len(utts)} → {len(result)}")
    return result


def get_tencent_segments(data: dict) -> list[dict]:
    """提取腾讯逗号级分段 → 统一格式。"""
    spk_map = {"0": "1", "1": "2", "2": "3", "3": "4"}
    segs = []
    for d in data["ResultDetail"]:
        text = d["FinalSentence"]
        text = re.sub(r'\[[^\]]+\]', '', text)
        emo_list = d.get("EmotionType", [])
        segs.append({
            "start_ms": d["StartMs"],
            "end_ms": d["EndMs"],
            "text": text,
            "speaker": spk_map.get(str(d["SpeakerId"]), "?"),
            "emotion": emo_list[0] if emo_list else None,
        })
    return segs


# ── LLM diff ─────────────────────────────────────────────────────────────

def call_llm_diff(primary_text: str, secondary_text: str, *, model_name: str, api_key: str) -> dict:
    """调 Gemini 比对主/副本 ASR 全文，返回 {diff, primary_sing, secondary_sing}。"""
    from google import genai
    from google.genai import types
    from dubora_pipeline.prompts import load_prompt

    rendered = load_prompt(
        "asr_diff",
        primary_text=primary_text,
        secondary_text=secondary_text,
    )

    client = genai.Client(api_key=api_key)
    info(f"LLM diff: calling {model_name}...")
    response = client.models.generate_content(
        model=model_name,
        contents=[
            types.Content(
                parts=[types.Part.from_text(text=rendered.user)],
                role="user",
            ),
        ],
        config=types.GenerateContentConfig(
            system_instruction=rendered.system,
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    result = json.loads(response.text)
    diff_count = len(result.get("diff", []))
    sing_count = len(result.get("primary_sing", [])) + len(result.get("secondary_sing", []))
    info(f"LLM diff: {diff_count} diffs, {sing_count} sing segments")
    return result


# ── 间隙检测 ─────────────────────────────────────────────────────────────

def find_gaps(utterances: list[dict], total_ms: int, min_gap_ms: int = 500) -> list[tuple]:
    gaps = []
    prev_end = 0
    for u in utterances:
        if u["start_ms"] - prev_end > min_gap_ms:
            gaps.append((prev_end, u["start_ms"]))
        prev_end = max(prev_end, u["end_ms"])
    if total_ms - prev_end > min_gap_ms:
        gaps.append((prev_end, total_ms))
    return gaps


def seg_in_gap(seg: dict, gap_start: int, gap_end: int, tolerance: int = 300) -> bool:
    return seg["start_ms"] >= gap_start - tolerance and seg["end_ms"] <= gap_end + tolerance


def is_duplicate(text: str, doubao_utts: list[dict]) -> bool:
    t = strip_punct(text)
    if not t:
        return True
    for u in doubao_utts:
        u_clean = strip_punct(u["text"])
        if not u_clean:
            continue
        if t in u_clean or u_clean in t:
            return True
        if lcs_ratio(t, u_clean, use_min=True) >= 0.6:
            return True
    return False


# ── Fish 字符→segment 映射 ──────────────────────────────────────────────

def _build_char_to_seg(full_text: str, segments: list[dict]) -> dict[int, int]:
    """构建 Fish 全文字符位置 → segment 索引的映射（跳过标点）。"""
    char_to_seg = {}
    seg_idx = 0
    for i, ch in enumerate(full_text):
        if _FISH_PUNC_RE.match(ch):
            continue
        if seg_idx < len(segments) and segments[seg_idx]["text"] == ch:
            char_to_seg[i] = seg_idx
            seg_idx += 1
    return char_to_seg


def _seg_range_ms(char_to_seg: dict, segments: list, start: int, end: int) -> tuple[int, int] | None:
    """通过字符偏移范围查找对应的时间范围。找不到返回 None。"""
    first = last = None
    for i in range(start, end):
        if i in char_to_seg:
            if first is None:
                first = char_to_seg[i]
            last = char_to_seg[i]
    if first is not None:
        return int(segments[first]["start"] * 1000), int(segments[last]["end"] * 1000)
    return None


# ── 歌曲时间范围 ─────────────────────────────────────────────────────────

def get_singing_ranges(
    doubao_utts: list[dict],
    fish_data: dict,
    primary_sing: list[dict],
    secondary_sing: list[dict],
) -> list[tuple[int, int]]:
    """将 LLM 返回的歌曲字符偏移转为时间范围 (start_ms, end_ms)。"""
    ranges = []

    # primary_sing: 字符偏移基于 doubao 拼接文本
    if primary_sing:
        char_to_utt = []
        for i, u in enumerate(doubao_utts):
            for _ in u["text"]:
                char_to_utt.append(i)
        for s in primary_sing:
            sc, ec = s["start"], s["end"] - 1
            if 0 <= sc < len(char_to_utt) and 0 <= ec < len(char_to_utt):
                ranges.append((doubao_utts[char_to_utt[sc]]["start_ms"],
                               doubao_utts[char_to_utt[ec]]["end_ms"]))

    # secondary_sing: 字符偏移基于 fish 全文
    if secondary_sing:
        full_text = fish_data.get("text", "")
        segments = fish_data.get("segments") or []
        if full_text and segments:
            char_to_seg = _build_char_to_seg(full_text, segments)
            for s in secondary_sing:
                r = _seg_range_ms(char_to_seg, segments, s["start"], s["end"])
                if r:
                    ranges.append(r)

    return ranges


# ── Fish 独有句子构建 ────────────────────────────────────────────────────

def _build_fish_unique(fish_data: dict, llm_diff: list, pause_ms: int = 500) -> list[dict]:
    """用 LLM diff 的 start/end 定位独有文本的时间范围。

    在标点处如果前后字符时间间隔 > pause_ms 则拆分。
    """
    full_text = fish_data.get("text", "")
    segments = fish_data.get("segments") or []
    if not full_text or not segments:
        return []

    char_to_seg = _build_char_to_seg(full_text, segments)

    result = []
    for d in llm_diff:
        text = d["text"]
        start, end = d["start"], d["end"]
        if full_text[start:end] != text:
            warning(f"LLM diff 校验失败: [{start}:{end}] = \"{full_text[start:end]}\" != \"{text}\"")
            continue
        # 在标点处按时间间隔拆分
        splits = _split_fish_by_pause(full_text, segments, char_to_seg, start, end, pause_ms)
        result.extend(splits)
    return result


def _split_fish_by_pause(
    full_text: str, segments: list, char_to_seg: dict,
    start: int, end: int, pause_ms: int,
) -> list[dict]:
    """Fish 句子在标点处按时间间隔拆分。"""
    text = full_text[start:end]
    # 找标点位置（相对 full_text 的偏移）
    split_points = []
    for i, ch in enumerate(text):
        if ch in _ALL_PUNCTS:
            abs_pos = start + i
            # 标点前最后一个有映射的字符
            prev_seg = None
            for p in range(abs_pos, start - 1, -1):
                if p in char_to_seg:
                    prev_seg = char_to_seg[p]
                    break
            # 标点后第一个有映射的字符
            next_seg = None
            for p in range(abs_pos + 1, end):
                if p in char_to_seg:
                    next_seg = char_to_seg[p]
                    break
            if prev_seg is not None and next_seg is not None:
                gap = int(segments[next_seg]["start"] * 1000) - int(segments[prev_seg]["end"] * 1000)
                if gap > pause_ms:
                    split_points.append(i + 1)  # 标点后断开

    if not split_points:
        r = _seg_range_ms(char_to_seg, segments, start, end)
        if r is not None:
            return [{"text": text, "start_ms": r[0], "end_ms": r[1]}]
        return []

    result = []
    prev = 0
    for sp in split_points:
        chunk = text[prev:sp]
        if chunk.strip():
            r = _seg_range_ms(char_to_seg, segments, start + prev, start + sp)
            if r is not None:
                result.append({"text": chunk, "start_ms": r[0], "end_ms": r[1]})
        prev = sp
    # 最后一段
    chunk = text[prev:]
    if chunk.strip():
        r = _seg_range_ms(char_to_seg, segments, start + prev, start + end - start)
        if r is not None:
            result.append({"text": chunk, "start_ms": r[0], "end_ms": r[1]})
    return result


# ── 核心融合 ─────────────────────────────────────────────────────────────

def fuse(doubao_utts: list[dict], tencent_segs: list[dict],
         fish_data: dict, llm_diff: list, total_ms: int) -> list[dict]:
    """三源融合：Doubao 主干 + 腾讯时间轴填空 + Fish 文本替换。

    返回间隙中填充的段（不含 Doubao 原始段，调用方自行合并）。
    """
    gaps = find_gaps(doubao_utts, total_ms)
    fish_unique = _build_fish_unique(fish_data, llm_diff)

    gap_tc = []
    for gs, ge in gaps:
        segs = [s for s in tencent_segs if seg_in_gap(s, gs, ge)]
        segs = [s for s in segs if not is_duplicate(s["text"], doubao_utts)]
        # tolerance 负责选进来，看 gap 内绝对时长决定保留，clamp 压回去
        clamped = []
        for s in segs:
            in_gap_ms = min(s["end_ms"], ge) - max(s["start_ms"], gs)
            if in_gap_ms < _MIN_IN_GAP_MS:
                continue
            s = dict(s)  # 不 mutate 原始 tencent_segs
            s["start_ms"] = max(s["start_ms"], gs)
            s["end_ms"] = min(s["end_ms"], ge)
            clamped.append(s)
        segs = clamped
        gap_tc.extend(segs)

    info(f"Fusion: {len(gaps)} gaps, {len(fish_unique)} fish unique, {len(gap_tc)} gap TC segments")

    filled = []
    tc_idx = 0

    for fs in fish_unique:
        if tc_idx >= len(gap_tc):
            spk = _find_nearest_speaker(fs["start_ms"], doubao_utts + filled)
            filled.append(_make_fish_seg(fs, spk))
            continue

        while tc_idx < len(gap_tc) and gap_tc[tc_idx]["end_ms"] < fs["start_ms"] - 15000:
            tc_idx += 1

        if tc_idx >= len(gap_tc):
            spk = _find_nearest_speaker(fs["start_ms"], doubao_utts + filled)
            filled.append(_make_fish_seg(fs, spk))
            continue

        if gap_tc[tc_idx]["start_ms"] > fs["end_ms"] + 15000:
            spk = _find_nearest_speaker(fs["start_ms"], doubao_utts + filled)
            filled.append(_make_fish_seg(fs, spk))
            continue

        best_ratio = 0.0
        best_end = tc_idx
        for j in range(tc_idx, min(tc_idx + 6, len(gap_tc))):
            group_text = "".join(s["text"] for s in gap_tc[tc_idx:j + 1])
            ratio = lcs_ratio(fs["text"], group_text)
            if ratio > best_ratio:
                best_ratio = ratio
                best_end = j
            if best_ratio >= 0.7:
                break

        if best_ratio >= 0.4:
            group = gap_tc[tc_idx:best_end + 1]
            merged = {
                "start_ms": group[0]["start_ms"],
                "end_ms": group[-1]["end_ms"],
                "speaker": group[0]["speaker"],
                "emotion": group[0].get("emotion"),
            }
            filled.append(_make_seg(merged, fs["text"], "tencent+fish"))
            tc_idx = best_end + 1
        else:
            filled.append(_make_seg(gap_tc[tc_idx], fs["text"], "tencent+fish"))
            tc_idx += 1

    info(f"Fusion: filled {len(filled)} segments in gaps")
    return filled


def _make_seg(tc_seg: dict, text: str, source: str) -> dict:
    return {
        "start_ms": tc_seg["start_ms"],
        "end_ms": tc_seg["end_ms"],
        "text": text,
        "speaker": tc_seg["speaker"],
        "emotion": tc_seg.get("emotion"),
        "gender": None,
        "source": source,
    }


def _make_fish_seg(fs: dict, speaker: str) -> dict:
    return {
        "start_ms": fs["start_ms"],
        "end_ms": fs["end_ms"],
        "text": fs["text"],
        "speaker": speaker,
        "emotion": None,
        "gender": None,
        "source": "fish",
    }


def _find_nearest_speaker(target_ms: int, segments: list[dict]) -> str:
    best_dist = float('inf')
    best_spk = "?"
    for seg in segments:
        dist = min(abs(seg["start_ms"] - target_ms), abs(seg["end_ms"] - target_ms))
        if dist < best_dist:
            best_dist = dist
            best_spk = seg["speaker"]
    return best_spk


# ── 重叠修复 ─────────────────────────────────────────────────────────────

def clamp_overlaps(merged: list[dict]) -> list[dict]:
    """修复重叠：Doubao 段不动，非 Doubao 段压缩到不重叠。"""
    for i in range(1, len(merged)):
        if merged[i]["start_ms"] < merged[i - 1]["end_ms"]:
            if merged[i].get("source") != "doubao":
                merged[i]["start_ms"] = merged[i - 1]["end_ms"]
            elif merged[i - 1].get("source") != "doubao":
                merged[i - 1]["end_ms"] = merged[i]["start_ms"]
    return [s for s in merged if s["start_ms"] < s["end_ms"]]


# ── Emotion 回填 ─────────────────────────────────────────────────────────

def fill_null_emotions(segments: list[dict]):
    """回填 null emotion：从相邻同 speaker 段继承，否则 neutral。原地修改。"""
    for i, seg in enumerate(segments):
        if seg.get("emotion") is not None:
            continue
        for j in (i - 1, i + 1, i - 2, i + 2):
            if 0 <= j < len(segments) and segments[j]["speaker"] == seg["speaker"]:
                if segments[j].get("emotion") and segments[j]["emotion"] != "neutral":
                    seg["emotion"] = segments[j]["emotion"]
                    break
        if seg.get("emotion") is None:
            seg["emotion"] = "neutral"


# ── end_ms 延长 ─────────────────────────────────────────────────────────

def extend_end_ms(
    segments: list[dict],
    *,
    min_display_ms: int = 1200,
    extend_ms: int = 200,
    min_gap_ms: int = 200,
) -> list[dict]:
    """规则级 end_ms 延长，保证字幕最小显示时长。"""
    result = []
    extended = 0
    for i, seg in enumerate(segments):
        new_seg = dict(seg)
        orig_end = seg["end_ms"]
        start = seg["start_ms"]

        desired = max(orig_end, start + min_display_ms, orig_end + extend_ms)

        if i < len(segments) - 1:
            max_end = segments[i + 1]["start_ms"] - min_gap_ms
            desired = min(desired, max_end)

        new_end = max(orig_end, desired)
        if new_end != orig_end:
            extended += 1
        new_seg["end_ms"] = new_end
        result.append(new_seg)

    if extended:
        info(f"Extended end_ms: {extended}/{len(segments)} segments")
    return result
