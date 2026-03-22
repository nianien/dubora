#!/usr/bin/env python3
"""ASR 三源融合测试 — Doubao 主干 + 腾讯时间轴 + Fish 文本。

Doubao 有的段原样保留（时间戳 + speaker + emotion + text）。
Doubao 空白区用腾讯段填时间轴和 speaker，Fish 文本替换腾讯文本。

用法：
  python test/test_asr_fusion.py
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fmt_time(ms):
    s = ms / 1000
    return f"{int(s // 60):02d}:{s % 60:06.3f}"


def strip_punct(s):
    return re.sub(r'[，。！？、；：""''（）…—\s,\.!?\-\[\]【】]', '', s)


# ── 提取各源数据 ─────────────────────────────────────────────────────────

def get_doubao_utterances(data):
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
        })
    return sorted(utts, key=lambda x: x["start_ms"])


def get_tencent_segments(data):
    """提取腾讯逗号级分段 → 统一格式。"""
    # 腾讯 SpeakerId → Doubao speaker 映射
    spk_map = {"0": "1", "1": "2", "2": "3", "3": "4"}
    segs = []
    for d in data["ResultDetail"]:
        text = d["FinalSentence"]
        # 去掉腾讯的情绪标签 [开心] [伤心] 等
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


def get_fish_sentences(data, comma_pause_ms=800):
    """Fish 全文切分：。！？始终断句，逗号处间隔>阈值才断句。"""
    full_text = data["text"]
    segments = data["segments"]

    def find_timestamps(text, start_seg):
        """找子句的首尾字符时间戳。"""
        chars = [c for c in text if not re.match(r'[，。！？、；：""''（）…—\s,\.!?\-]', c)]
        if not chars:
            return None, None, start_seg
        first = last = None
        matched = 0
        scan = start_seg
        while scan < len(segments) and matched < len(chars):
            if segments[scan]["text"] == chars[matched]:
                if first is None:
                    first = scan
                last = scan
                matched += 1
            scan += 1
        if first is not None and matched > 0:
            return int(segments[first]["start"] * 1000), int(segments[last]["end"] * 1000), last + 1
        return None, None, start_seg

    # 先按句号级标点切分（始终断句）
    sentences = re.split(r'(?<=[。！？!?])', full_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    result = []
    seg_idx = 0
    for sent in sentences:
        # 句内按逗号切成子句
        clauses = re.split(r'(?<=[，,、；：])', sent)
        clauses = [c for c in clauses if c.strip()]

        # 每个子句找时间戳
        clause_info = []
        for clause in clauses:
            s_ms, e_ms, seg_idx = find_timestamps(clause, seg_idx)
            clause_info.append({"text": clause, "start_ms": s_ms or 0, "end_ms": e_ms or 0})

        # 逗号处间隔 ≤ 阈值则合并，> 阈值则断开
        merged = []
        for ci in clause_info:
            if merged and ci["start_ms"] - merged[-1]["end_ms"] <= comma_pause_ms:
                merged[-1]["text"] += ci["text"]
                merged[-1]["end_ms"] = ci["end_ms"]
            else:
                merged.append(dict(ci))
        result.extend(merged)

    return result


# ── 间隙检测 ─────────────────────────────────────────────────────────────

def find_gaps(utterances, total_ms, min_gap_ms=500):
    gaps = []
    prev_end = 0
    for u in utterances:
        if u["start_ms"] - prev_end > min_gap_ms:
            gaps.append((prev_end, u["start_ms"]))
        prev_end = max(prev_end, u["end_ms"])
    if total_ms - prev_end > min_gap_ms:
        gaps.append((prev_end, total_ms))
    return gaps


def seg_in_gap(seg, gap_start, gap_end, tolerance=300):
    return seg["start_ms"] >= gap_start - tolerance and seg["end_ms"] <= gap_end + tolerance


def is_duplicate(text, doubao_utts):
    t = strip_punct(text)
    if not t:
        return True
    for u in doubao_utts:
        u_clean = strip_punct(u["text"])
        if not u_clean:
            continue
        # 子串匹配
        if t in u_clean or u_clean in t:
            return True
        # LCS 模糊匹配（"自己返乡"≈"知青返乡"）
        if lcs_ratio(t, u_clean, use_min=True) >= 0.6:
            return True
    return False


# ── LLM diff → Fish 独有句子（用字符偏移定位时间戳）────────────────────

def _build_fish_unique(fish_data, llm_diff, comma_pause_ms=800):
    """用 LLM diff 的 start/end 定位独有文本，再按逗号停顿拆分。"""
    full_text = fish_data["text"]
    segments = fish_data["segments"]

    # 建立字符位置 → segment 索引的映射
    char_to_seg = {}
    seg_idx = 0
    punct_re = re.compile(r'[，。！？、；：""''（）…—\s,\.!?\-]')
    for i, ch in enumerate(full_text):
        if punct_re.match(ch):
            continue
        if seg_idx < len(segments) and segments[seg_idx]["text"] == ch:
            char_to_seg[i] = seg_idx
            seg_idx += 1

    def get_timestamps(char_start, char_end):
        first_seg = last_seg = None
        for i in range(char_start, char_end):
            if i in char_to_seg:
                if first_seg is None:
                    first_seg = char_to_seg[i]
                last_seg = char_to_seg[i]
        if first_seg is not None:
            return int(segments[first_seg]["start"] * 1000), int(segments[last_seg]["end"] * 1000)
        return 0, 0

    result = []
    for d in llm_diff:
        text = d["text"]
        start, end = d["start"], d["end"]
        if full_text[start:end] != text:
            print(f"  !! LLM diff 校验失败: [{start}:{end}] = \"{full_text[start:end]}\" != \"{text}\"")
            continue

        # 按。！？切分（LLM 通常已按句输出，但保险起见）
        sentences = re.split(r'(?<=[。！？!?])', text)
        sentences = [s for s in sentences if s.strip()]

        sent_offset = start
        for sent in sentences:
            sent_start = sent_offset
            sent_offset += len(sent)

            # 句内按逗号切子句，检查停顿
            clauses = re.split(r'(?<=[，,、；：])', sent)
            clauses = [c for c in clauses if c.strip()]

            clause_info = []
            c_offset = sent_start
            for clause in clauses:
                c_start = c_offset
                c_end = c_start + len(clause)
                s_ms, e_ms = get_timestamps(c_start, c_end)
                clause_info.append({"text": clause, "start_ms": s_ms, "end_ms": e_ms})
                c_offset = c_end

            # 逗号处间隔 ≤ 阈值则合并，> 阈值则断开
            merged = []
            for ci in clause_info:
                if merged and ci["start_ms"] - merged[-1]["end_ms"] <= comma_pause_ms:
                    merged[-1]["text"] += ci["text"]
                    merged[-1]["end_ms"] = ci["end_ms"]
                else:
                    merged.append(dict(ci))

            for m in merged:
                result.append({
                    "text": m["text"],
                    "start_ms": m["start_ms"],
                    "end_ms": m["end_ms"],
                })
    return result


# ── Fish 文本匹配 + 拆分 ────────────────────────────────────────────────

def lcs_ratio(a, b, use_min=False):
    """最长公共子序列长度 / 串长度。use_min=True 用较短串（去重），False 用较长串（匹配）。"""
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



# ── 核心融合 ─────────────────────────────────────────────────────────────

def fuse(doubao_utts, tencent_segs, fish_data, llm_diff, total_ms):
    """三源融合：Doubao 主干 + 腾讯时间轴填空 + Fish 文本替换。

    扁平化所有 Doubao 间隙中的 TC 段，和 Fish 独有句子做一趟顺序匹配。
    """
    gaps = find_gaps(doubao_utts, total_ms)
    fish_unique = _build_fish_unique(fish_data, llm_diff)

    # 扁平化：收集所有间隙中的 TC 段（去重），按时间排序
    gap_tc = []
    for gs, ge in gaps:
        segs = [s for s in tencent_segs if seg_in_gap(s, gs, ge)]
        segs = [s for s in segs if not is_duplicate(s["text"], doubao_utts)]
        gap_tc.extend(segs)

    print(f"=== 间隙 ({len(gaps)} 个) ===")
    for gs, ge in gaps:
        print(f"  {fmt_time(gs)} ~ {fmt_time(ge)}  ({(ge - gs) / 1000:.1f}s)")

    print(f"\n=== Fish 独有 ({len(fish_unique)}) / TC 窗口 ({len(gap_tc)}) ===")
    for i, fs in enumerate(fish_unique):
        print(f"  Fish[{i:2d}] {fmt_time(fs['start_ms'])}~{fmt_time(fs['end_ms'])}  {fs['text']}")
    for i, tc in enumerate(gap_tc):
        print(f"  TC[{i:2d}]   {fmt_time(tc['start_ms'])}~{fmt_time(tc['end_ms'])}  {tc['text']}")

    # 一趟顺序匹配：Fish 和 TC 都按时间排序，逐个消费
    filled = []
    tc_idx = 0

    for fi, fs in enumerate(fish_unique):
        if tc_idx >= len(gap_tc):
            # TC 用完，剩余 Fish 用自身时间戳
            spk = _find_nearest_speaker(fs["start_ms"], doubao_utts + filled)
            filled.append(_make_fish_seg(fs, spk))
            print(f"  孤儿: Fish[{fi}] \"{fs['text'][:20]}\" (TC用完)")
            continue

        # 跳过远早于当前 Fish 的 TC 段（TC.end 比 Fish.start 早 15s 以上）
        while tc_idx < len(gap_tc) and gap_tc[tc_idx]["end_ms"] < fs["start_ms"] - 15000:
            tc_idx += 1

        if tc_idx >= len(gap_tc):
            spk = _find_nearest_speaker(fs["start_ms"], doubao_utts + filled)
            filled.append(_make_fish_seg(fs, spk))
            print(f"  孤儿: Fish[{fi}] \"{fs['text'][:20]}\" (TC用完)")
            continue

        # 当前 TC 远晚于 Fish（TC.start 比 Fish.end 晚 15s 以上）→ 无 TC 可用
        if gap_tc[tc_idx]["start_ms"] > fs["end_ms"] + 15000:
            spk = _find_nearest_speaker(fs["start_ms"], doubao_utts + filled)
            filled.append(_make_fish_seg(fs, spk))
            print(f"  孤儿: Fish[{fi}] \"{fs['text'][:20]}\" (无近TC)")
            continue

        # LCS 匹配：尝试 1~6 个连续 TC 段
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
            # LCS 匹配：用匹配到的 TC 组时间窗口
            group = gap_tc[tc_idx:best_end + 1]
            merged = {
                "start_ms": group[0]["start_ms"],
                "end_ms": group[-1]["end_ms"],
                "speaker": group[0]["speaker"],
                "emotion": group[0].get("emotion"),
            }
            filled.append(_make_seg(merged, fs["text"], "tencent+fish"))
            print(f"  匹配: Fish[{fi}] \"{fs['text'][:20]}\" → "
                  f"TC[{tc_idx}:{best_end + 1}] ratio={best_ratio:.2f}")
            tc_idx = best_end + 1
        else:
            # 强制：只吃 1 个 TC 段
            filled.append(_make_seg(gap_tc[tc_idx], fs["text"], "tencent+fish"))
            print(f"  强制: Fish[{fi}] \"{fs['text'][:20]}\" → TC[{tc_idx}] ratio={best_ratio:.2f}")
            tc_idx += 1

    return filled


def _make_fish_seg(fs, speaker):
    return {
        "start_ms": fs["start_ms"],
        "end_ms": fs["end_ms"],
        "text": fs["text"],
        "speaker": speaker,
        "emotion": None,
        "gender": None,
        "source": "fish",
    }


def _find_nearest_speaker(target_ms, segments):
    """找时间上最近的片段的 speaker。"""
    best_dist = float('inf')
    best_spk = "?"
    for seg in segments:
        dist = min(abs(seg["start_ms"] - target_ms), abs(seg["end_ms"] - target_ms))
        if dist < best_dist:
            best_dist = dist
            best_spk = seg["speaker"]
    return best_spk


def _make_seg(tc_seg, text, source):
    return {
        "start_ms": tc_seg["start_ms"],
        "end_ms": tc_seg["end_ms"],
        "text": text,
        "speaker": tc_seg["speaker"],
        "emotion": tc_seg.get("emotion"),
        "gender": None,
        "source": source,
    }


# ── 输出 ──────────────────────────────────────────────────────────────────

def print_merged(merged):
    print(f"\n=== 融合结果 ({len(merged)} 段) ===")
    for i, item in enumerate(merged):
        src = item["source"]
        if src == "doubao":
            tag = "D"
        elif src == "tencent+fish":
            tag = "TF"
        else:
            tag = "T"
        spk = item.get("speaker", "?")
        emo = item.get("emotion") or "-"
        sing_mark = " [歌]" if item.get("type") == "sing" else ""
        print(f"  [{tag:>2s}] {fmt_time(item['start_ms'])} ~ {fmt_time(item['end_ms'])}  "
              f"spk={spk:>2s}  {emo:<10s}  {item['text']}{sing_mark}")

    print()
    speakers = defaultdict(int)
    for item in merged:
        speakers[item["speaker"]] += 1
    print(f"Speakers: {dict(speakers)}")

    src_count = defaultdict(int)
    for item in merged:
        src_count[item["source"]] += 1
    print(f"来源: {dict(src_count)}")

    overlaps = 0
    for i in range(1, len(merged)):
        if merged[i]["start_ms"] < merged[i - 1]["end_ms"]:
            overlaps += 1
            print(f"  !! 重叠: [{i-1}] end={merged[i-1]['end_ms']} > [{i}] start={merged[i]['start_ms']}")
    if not overlaps:
        print("重叠: 无")


def main():
    parser = argparse.ArgumentParser(description="ASR 三源融合 — Doubao + Tencent + Fish")
    parser.add_argument("--doubao", default="test_out/asr/6_doubao.json")
    parser.add_argument("--tencent", default="test_out/asr/6_tencent.json")
    parser.add_argument("--fish", default="test_out/asr/6_fish.json")
    parser.add_argument("--llm-diff", default="test_out/asr/6_llm_diff.json")
    parser.add_argument("-o", "--output", default="test_out/asr/6_fusion.json")
    args = parser.parse_args()

    for p in [args.doubao, args.tencent, args.fish, args.llm_diff]:
        if not Path(p).exists():
            print(f"文件不存在: {p}", file=sys.stderr)
            sys.exit(1)

    doubao_data = load_json(args.doubao)
    tencent_data = load_json(args.tencent)
    fish_data = load_json(args.fish)

    total_ms = doubao_data["audio_info"]["duration"]
    doubao_utts = get_doubao_utterances(doubao_data)
    tencent_segs = get_tencent_segments(tencent_data)
    llm_result = load_json(args.llm_diff)
    llm_diff = llm_result["diff"]

    print(f"Doubao: {len(doubao_utts)} 段")
    print(f"Tencent: {len(tencent_segs)} 段")
    print(f"LLM diff: {len(llm_diff)} 条")
    print(f"歌曲标注: 主本 {len(llm_result.get('primary_sing', []))} 段, "
          f"副本 {len(llm_result.get('secondary_sing', []))} 段")
    print()

    filled = fuse(doubao_utts, tencent_segs, fish_data, llm_diff, total_ms)
    merged = sorted(doubao_utts + filled, key=lambda x: x["start_ms"])

    # 歌曲标注：只给命中的段打 sing，其余不加 type
    singing_texts = [strip_punct(s["text"]) for s in
                     llm_result.get("primary_sing", []) + llm_result.get("secondary_sing", [])]
    for seg in merged:
        sc = strip_punct(seg["text"])
        if any(st in sc or sc in st for st in singing_texts):
            seg["type"] = "sing"
    print_merged(merged)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"\n保存: {out_path}")


if __name__ == "__main__":
    main()
