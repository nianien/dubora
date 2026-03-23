"""ASR 三源融合处理器

Doubao 主干 + 腾讯时间轴填空 + Fish 文本替换。

公共 API:
- get_doubao_utterances(): 提取 Doubao 统一段
- get_tencent_segments(): 提取腾讯逗号级分段
- call_llm_diff(): LLM 比对主/副本文本差异
- call_llm_align(): LLM 对齐 Fish 文本到腾讯时间窗口
- build_align_input(): 构建 LLM 对齐输入
- apply_alignment(): 应用 LLM 对齐结果
- fill_null_emotions(): emotion 回填
- extend_end_ms(): end_ms 延长
- clamp_overlaps(): 重叠修复
"""
import json
import re

from dubora_core.utils.logger import info, warning


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
    """调 Gemini 比对主/副本 ASR 全文，返回 {diff}。"""
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
    info(f"LLM diff: {diff_count} diffs")
    return result


# ── LLM align ────────────────────────────────────────────────────────────

def call_llm_align(align_input: dict, *, model_name: str, api_key: str) -> dict:
    """调 Gemini 对齐 Fish 文本到腾讯时间窗口，返回 {aligned, unmatched_windows}。"""
    from google import genai
    from google.genai import types
    from dubora_pipeline.prompts import load_prompt

    input_json = json.dumps(align_input, ensure_ascii=False, indent=2)
    rendered = load_prompt("asr_align", input_json=input_json)

    client = genai.Client(api_key=api_key)
    info(f"LLM align: calling {model_name}...")
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
    raw = json.loads(response.text)
    # LLM 可能直接返回数组，兼容处理
    if isinstance(raw, list):
        result = {"aligned": raw, "unmatched_windows": []}
    else:
        result = raw
    aligned = result.get("aligned", [])
    unmatched = result.get("unmatched_windows", [])
    info(f"LLM align: {len(aligned)} aligned, {len(unmatched)} unmatched tencent")
    for u in unmatched:
        info(f"  unmatched TC: {u.get('start_ms')}~{u.get('end_ms')}ms \"{u.get('text')}\" ({u.get('reason')})")
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


# ── 核心融合 ─────────────────────────────────────────────────────────────

def _extract_fish_timestamps(fish_data: dict, llm_diff: list) -> list[dict]:
    """从 Fish ASR 数据提取 diff 条目的时间戳（近似参考）。"""
    full_text = fish_data.get("text", "")
    segments = fish_data.get("segments") or []
    if not full_text or not segments:
        return [{"text": d["text"]} for d in llm_diff]

    # 字符位置 → segment 索引（跳过标点）
    punct_re = re.compile(r'[，。！？、；：""''（）…—\s,\.!?\-\[\]【】]')
    char_to_seg = {}
    seg_idx = 0
    for i, ch in enumerate(full_text):
        if punct_re.match(ch):
            continue
        if seg_idx < len(segments) and segments[seg_idx]["text"] == ch:
            char_to_seg[i] = seg_idx
            seg_idx += 1

    result = []
    for d in llm_diff:
        text = d["text"]
        start, end = d["start"], d["end"]
        if start < len(full_text) and full_text[start:end] != text:
            warning(f"LLM diff offset mismatch: [{start}:{end}] != \"{text}\"")
            result.append({"text": text})
            continue
        first_seg = last_seg = None
        for i in range(start, end):
            if i in char_to_seg:
                if first_seg is None:
                    first_seg = char_to_seg[i]
                last_seg = char_to_seg[i]
        if first_seg is not None:
            result.append({
                "text": text,
                "start_ms": int(segments[first_seg]["start"] * 1000),
                "end_ms": int(segments[last_seg]["end"] * 1000),
            })
        else:
            result.append({"text": text})
    return result


def build_align_input(doubao_utts: list[dict], tencent_segs: list[dict],
                      llm_diff: list, total_ms: int,
                      fish_data: dict | None = None) -> dict | None:
    """构建 LLM 对齐输入。无需对齐时返回 None。"""
    if not llm_diff:
        info("Fusion: no fish unique texts, skip")
        return None

    text_unique = _extract_fish_timestamps(fish_data, llm_diff) if fish_data else [{"text": d["text"]} for d in llm_diff]

    gaps = find_gaps(doubao_utts, total_ms)

    gaps_data = []
    for i, (gs, ge) in enumerate(gaps):
        segs = [s for s in tencent_segs if seg_in_gap(s, gs, ge)]
        if not segs:
            continue
        gaps_data.append({
            "gap_id": i + 1,
            "start_ms": gs,
            "end_ms": ge,
            "windows": [
                {"start_ms": s["start_ms"], "end_ms": s["end_ms"], "text": s["text"]}
                for s in segs
            ],
        })

    info(f"Fusion: {len(gaps)} gaps, {len(gaps_data)} with tencent, {len(text_unique)} fish unique")

    if not gaps_data:
        info("Fusion: no gaps with tencent data, skip alignment")
        return None

    return {"gaps": gaps_data, "text_unique": text_unique}


def apply_alignment(aligned: list[dict], tencent_segs: list[dict],
                    doubao_utts: list[dict], total_ms: int) -> list[dict]:
    """将 LLM 对齐结果转为 segment 列表。

    返回间隙中填充的段（不含 Doubao 原始段，调用方自行合并）。
    未匹配的 fish 段通过前后锚点间的空闲 TC 窗口分配，无空闲窗口则丢弃。
    """
    gaps = find_gaps(doubao_utts, total_ms)

    # 收集所有 gap 内的 TC 窗口
    gap_tc = []
    for gs, ge in gaps:
        for s in tencent_segs:
            if seg_in_gap(s, gs, ge):
                gap_tc.append(s)
    gap_tc.sort(key=lambda x: x["start_ms"])

    # 第一遍：构建 filled，记录已用 TC 窗口
    filled = []
    tc_used = set()
    for seg in aligned:
        text = seg["text"]
        start_ms = seg.get("start_ms")
        end_ms = seg.get("end_ms")
        source = seg.get("source", "text")

        if source == "window+text" and start_ms is not None and end_ms is not None:
            speaker = _find_speaker_by_time(start_ms, end_ms, tencent_segs)
            filled.append({
                "start_ms": start_ms, "end_ms": end_ms,
                "text": text, "speaker": speaker,
                "emotion": None, "gender": None, "source": source,
            })
            tc_used.add((start_ms, end_ms))
        else:
            filled.append({
                "start_ms": None, "end_ms": None,
                "text": text, "speaker": None,
                "emotion": None, "gender": None, "source": "text",
            })

    # 第二遍：未匹配 fish 段 → 找前后锚点间空闲 TC 窗口
    result = []
    i = 0
    while i < len(filled):
        if filled[i]["start_ms"] is not None:
            result.append(filled[i])
            i += 1
            continue

        # 收集连续 null 段
        group_start = i
        while i < len(filled) and filled[i]["start_ms"] is None:
            i += 1
        null_group = filled[group_start:i]

        # 前锚点 end_ms
        prev_end = 0
        for j in range(group_start - 1, -1, -1):
            if filled[j]["end_ms"] is not None:
                prev_end = filled[j]["end_ms"]
                break

        # 后锚点 start_ms
        next_start = float("inf")
        for j in range(i, len(filled)):
            if filled[j]["start_ms"] is not None:
                next_start = filled[j]["start_ms"]
                break

        # 空闲 TC 窗口：在锚点之间且未被使用
        free_tc = [
            tc for tc in gap_tc
            if tc["start_ms"] >= prev_end
            and tc["end_ms"] <= next_start
            and (tc["start_ms"], tc["end_ms"]) not in tc_used
        ]
        free_tc.sort(key=lambda x: x["start_ms"])

        # 按顺序分配空闲窗口，无窗口则丢弃
        for k, fish_seg in enumerate(null_group):
            if k < len(free_tc):
                tc = free_tc[k]
                fish_seg["start_ms"] = tc["start_ms"]
                fish_seg["end_ms"] = tc["end_ms"]
                fish_seg["speaker"] = tc.get("speaker", "?")
                fish_seg["source"] = "window+text"
                tc_used.add((tc["start_ms"], tc["end_ms"]))
                result.append(fish_seg)
                info(f"Fusion: fish \"{fish_seg['text']}\" → TC {tc['start_ms']}~{tc['end_ms']}ms")
            else:
                info(f"Fusion: discard fish \"{fish_seg['text']}\" (no free TC window)")

    info(f"Fusion: filled {len(result)} segments")
    return result


def _find_speaker_by_time(start_ms: int, end_ms: int, tencent_segs: list[dict]) -> str:
    """按时间重叠找腾讯段的 speaker。"""
    best_overlap = 0
    best_spk = "?"
    for s in tencent_segs:
        overlap = min(end_ms, s["end_ms"]) - max(start_ms, s["start_ms"])
        if overlap > best_overlap:
            best_overlap = overlap
            best_spk = s["speaker"]
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
