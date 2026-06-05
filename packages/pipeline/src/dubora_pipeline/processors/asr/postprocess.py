"""ASR 后处理工具：从豆包原始响应提取 utterances + emotion 回填 + end_ms 延长。

单源豆包方案下，parse phase 直接用这些工具把 doubao raw 转成 cue rows。
"""
from dubora_core.utils.logger import info


def get_doubao_utterances(data: dict) -> list[dict]:
    """提取 Doubao utterances → 统一格式。

    speaker 默认 "0"（豆包未识别说话人时的兜底，对应未分配 role 的物理声源 label）。
    """
    utts = []
    for u in data["result"]["utterances"]:
        adds = u.get("additions", {})
        utts.append({
            "start_ms": u["start_time"],
            "end_ms": u["end_time"],
            "text": u["text"],
            "speaker": str(adds.get("speaker", "0")),
            "emotion": adds.get("emotion"),
            "gender": adds.get("gender"),
        })
    return sorted(utts, key=lambda x: x["start_ms"])


def fill_null_emotions(segments: list[dict]) -> None:
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
