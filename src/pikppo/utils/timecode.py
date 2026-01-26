from datetime import timedelta
from pathlib import Path
from typing import Iterable, Mapping


def srt_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp (HH:MM:SS,mmm)."""
    td = timedelta(seconds=max(0.0, seconds))
    total_ms = int(td.total_seconds() * 1000)
    hh = total_ms // 3_600_000
    mm = (total_ms % 3_600_000) // 60_000
    ss = (total_ms % 60_000) // 1_000
    ms = total_ms % 1_000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def write_srt_from_segments(
    segments: Iterable[Mapping],
    out_path: str,
    *,
    text_key: str = "text",
    include_speaker: bool = False,
) -> None:
    """
    Write a simple SRT file from Whisper-like segments.

    segments: iterable of dicts with keys: start, end, text_key
    Tolerant to different field names: tries text_key, then "text", then "sentence", then "transcript"
    include_speaker: if True, prefix text with [Speaker X] when speaker info is available
    """
    lines: list[str] = []
    index = 1
    for seg in segments:
        start = srt_timestamp(float(seg["start"]))
        end = srt_timestamp(float(seg["end"]))
        # Tolerant text extraction: try multiple possible keys
        text = (
            seg.get(text_key)
            or seg.get("text")
            or seg.get("sentence")
            or seg.get("transcript")
            or ""
        )
        text = str(text).strip() if text else ""
        if not text:
            continue
        
        # 如果启用 speaker 信息且存在，在文本前添加 [Speaker X]
        if include_speaker and "speaker" in seg:
            speaker = seg.get("speaker", "")
            if speaker and speaker != "speaker_0":  # 避免显示默认值
                text = f"[{speaker}] {text}"
        
        lines.append(str(index))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")  # blank line
        index += 1

    # Ensure output directory exists
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


