#!/usr/bin/env python3
"""Forced Alignment 测试 — 用 torchaudio MMS_FA 对整段音频做全文对齐。

直接把完整音频 + Fish 全文喂给 FA，一次性对齐所有字符，
不依赖 Fish 的时间戳。

用法：
  python test/test_forced_alignment.py
  python test/test_forced_alignment.py --audio data/pipeline/家里家外/6/6-vocals.wav
"""

import argparse
import json
import re
import sys
from pathlib import Path

import torch
import torchaudio
from pypinyin import pinyin, Style


def fmt_time(ms):
    s = ms / 1000
    return f"{int(s // 60):02d}:{s % 60:06.3f}"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def chinese_to_pinyin(text):
    """中文文本 → (chars, pinyin_tokens)。去掉标点，每个汉字对应一个拼音。"""
    clean = re.sub(r'[，。！？、；：""''（）…—\s,\.!?\-]', '', text)
    chars = list(clean)
    tokens = []
    for ch in chars:
        py = pinyin(ch, style=Style.NORMAL)[0][0]
        py_clean = re.sub(r'[^a-z]', '', py.lower())
        tokens.append(py_clean if py_clean else ch.lower())
    return chars, tokens


def full_align(model, bundle, waveform, sample_rate, text, device):
    """整段音频 + 全文一次性 forced alignment → 字符级时间戳。

    返回: [(char, start_ms, end_ms, score), ...]
    """
    model_sr = bundle.sample_rate
    if sample_rate != model_sr:
        waveform = torchaudio.functional.resample(waveform, sample_rate, model_sr)

    if waveform.ndim == 2 and waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    elif waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)

    chars, pinyin_tokens = chinese_to_pinyin(text)
    if not pinyin_tokens:
        return []

    dictionary = bundle.get_dict()

    # 拼音 → token IDs，记录每个汉字对应的 token 范围
    token_ids = []
    char_to_token_range = []
    for py in pinyin_tokens:
        start_idx = len(token_ids)
        for c in py:
            if c in dictionary:
                token_ids.append(dictionary[c])
        char_to_token_range.append((start_idx, len(token_ids)))

    if not token_ids:
        return []

    print(f"  音频帧数推理中... ({waveform.shape[1]} samples)")
    with torch.inference_mode():
        emission, _ = model(waveform.to(device))
    print(f"  emission: {emission.shape} (帧数={emission.size(1)})")

    targets = torch.tensor([token_ids], dtype=torch.int32, device=device)
    print(f"  token 数: {len(token_ids)}, 字符数: {len(chars)}")

    try:
        alignments, scores = torchaudio.functional.forced_align(
            emission, targets, blank=0
        )
    except Exception as e:
        print(f"  对齐失败: {e}")
        return []

    aligned = alignments[0]
    scores_exp = scores[0].exp()
    token_spans = torchaudio.functional.merge_tokens(aligned, scores_exp)

    # 帧 → 秒
    num_frames = emission.size(1)
    ratio = waveform.size(1) / num_frames / model_sr

    # token-level → 字符级
    results = []
    for char, (t_start, t_end) in zip(chars, char_to_token_range):
        if t_start >= t_end or t_start >= len(token_spans):
            continue
        first_span = token_spans[t_start]
        last_span = token_spans[min(t_end - 1, len(token_spans) - 1)]
        start_ms = int(first_span.start * ratio * 1000)
        end_ms = int(last_span.end * ratio * 1000)
        avg_score = sum(token_spans[j].score for j in range(t_start, min(t_end, len(token_spans)))) / (t_end - t_start)
        results.append((char, start_ms, end_ms, avg_score))

    return results


def group_into_sentences(char_timestamps, full_text):
    """用原文标点把字符级时间戳重新组成句子级。

    返回: [{"text": ..., "start_ms": ..., "end_ms": ..., "avg_score": ...}, ...]
    """
    # 按标点切分原文
    sentences = re.split(r'(?<=[。！？!?])', full_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    result = []
    char_idx = 0
    for sent in sentences:
        # 这个句子有多少个非标点字符
        clean = re.sub(r'[，。！？、；：""''（）…—\s,\.!?\-]', '', sent)
        n_chars = len(clean)
        if n_chars == 0:
            continue

        if char_idx >= len(char_timestamps):
            break

        # 取对应的字符时间戳
        end_idx = min(char_idx + n_chars, len(char_timestamps))
        chunk = char_timestamps[char_idx:end_idx]
        if not chunk:
            char_idx = end_idx
            continue

        result.append({
            "text": sent,
            "start_ms": chunk[0][1],
            "end_ms": chunk[-1][2],
            "avg_score": sum(c[3] for c in chunk) / len(chunk),
            "chars": [(c[0], c[1], c[2], round(c[3], 3)) for c in chunk],
        })
        char_idx = end_idx

    return result


def main():
    parser = argparse.ArgumentParser(description="Forced Alignment 测试 — 全文对齐")
    parser.add_argument("--audio", default="data/pipeline/家里家外/6/6-vocals.wav")
    parser.add_argument("--fish", default="test_out/asr/6_fish.json")
    parser.add_argument("-o", "--output", default="test_out/asr/6_fa.json")
    args = parser.parse_args()

    for p in [args.audio, args.fish]:
        if not Path(p).exists():
            print(f"文件不存在: {p}", file=sys.stderr)
            sys.exit(1)

    # 加载音频
    print(f"加载音频: {args.audio}")
    waveform, sample_rate = torchaudio.load(args.audio)
    total_ms = int(waveform.shape[1] / sample_rate * 1000)
    print(f"  采样率: {sample_rate}  时长: {fmt_time(total_ms)}")

    # 加载 Fish 全文
    fish_data = load_json(args.fish)
    full_text = fish_data["text"]
    print(f"  Fish 全文: {len(full_text)} 字符")

    # 加载模型
    print("加载 MMS_FA 模型...")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model().to(device)
    print(f"  设备: {device}")

    # 全文一次性对齐
    print("\n=== 全文 Forced Alignment ===")
    char_timestamps = full_align(model, bundle, waveform, sample_rate, full_text, device)
    print(f"  对齐字符数: {len(char_timestamps)}")

    if not char_timestamps:
        print("对齐失败", file=sys.stderr)
        sys.exit(1)

    # 按句子分组
    sentences = group_into_sentences(char_timestamps, full_text)
    print(f"\n=== FA 句子级结果 ({len(sentences)} 句) ===")
    for i, s in enumerate(sentences):
        print(f"  {i+1:2d}. {fmt_time(s['start_ms'])} ~ {fmt_time(s['end_ms'])}  "
              f"score={s['avg_score']:.3f}  {s['text']}")

    # 和 Fish 原始时间戳对比
    fish_sentences = _get_fish_sentences(fish_data)
    if fish_sentences:
        print(f"\n=== FA vs Fish 时间戳对比 ===")
        print(f"{'句子':<20s}  {'Fish start':>10s}  {'FA start':>10s}  {'diff':>8s}  "
              f"{'Fish end':>10s}  {'FA end':>10s}  {'diff':>8s}")
        print("-" * 95)
        for fa_s in sentences:
            # 找 Fish 中文本最匹配的句子
            fa_clean = re.sub(r'[，。！？、；：""''（）…—\s,\.!?\-]', '', fa_s["text"])
            best = None
            for fs in fish_sentences:
                fs_clean = re.sub(r'[，。！？、；：""''（）…—\s,\.!?\-]', '', fs["text"])
                if fa_clean == fs_clean or fa_clean in fs_clean or fs_clean in fa_clean:
                    best = fs
                    break
            if best:
                sd = fa_s["start_ms"] - best["start_ms"]
                ed = fa_s["end_ms"] - best["end_ms"]
                text_short = fa_s["text"][:18]
                print(f"  {text_short:<18s}  {fmt_time(best['start_ms']):>10s}  "
                      f"{fmt_time(fa_s['start_ms']):>10s}  {sd:>+7d}ms  "
                      f"{fmt_time(best['end_ms']):>10s}  {fmt_time(fa_s['end_ms']):>10s}  {ed:>+7d}ms")

    # 保存
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "total_ms": total_ms,
        "sentences": [
            {
                "text": s["text"],
                "start_ms": s["start_ms"],
                "end_ms": s["end_ms"],
                "avg_score": round(s["avg_score"], 4),
            }
            for s in sentences
        ],
        "char_timestamps": [
            {"char": c, "start_ms": s, "end_ms": e, "score": round(sc, 4)}
            for c, s, e, sc in char_timestamps
        ],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n保存: {out_path}")


def _get_fish_sentences(data):
    """从 Fish 结果提取句子级分段（复用 test_asr_fusion 的逻辑）。"""
    full_text = data["text"]
    segments = data["segments"]

    sentences = re.split(r'(?<=[。！？!?])', full_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    result = []
    seg_idx = 0
    for sent in sentences:
        chars = [c for c in sent if not re.match(r'[，。！？、；：""''（）…—\s,\.!?\-]', c)]
        if not chars:
            continue

        first_seg = None
        last_seg = None
        matched = 0
        scan = seg_idx
        while scan < len(segments) and matched < len(chars):
            if segments[scan]["text"] == chars[matched]:
                if first_seg is None:
                    first_seg = scan
                last_seg = scan
                matched += 1
            scan += 1

        if first_seg is None or matched == 0:
            continue

        start_ms = int(segments[first_seg]["start"] * 1000)
        end_ms = int(segments[last_seg]["end"] * 1000)
        if end_ms <= start_ms and last_seg + 1 < len(segments):
            end_ms = int(segments[last_seg + 1]["start"] * 1000)
        if end_ms <= start_ms:
            end_ms = start_ms + 200

        result.append({"start_ms": start_ms, "end_ms": end_ms, "text": sent})
        seg_idx = last_seg + 1

    return result


if __name__ == "__main__":
    main()
