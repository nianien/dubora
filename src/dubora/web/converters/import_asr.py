"""
Import: asr-result.json → AsrModel

复用 doubao parser 解析 ASR 原始响应，转为 AsrModel 结构。
"""
from datetime import datetime, timezone
from typing import Any, Dict

from dubora.models.doubao.parser import parse_utterances
from dubora.schema.asr_model import (
    AsrModel,
    AsrMediaInfo,
    AsrSegment,
    AsrHistory,
    _gen_seg_id,
)


def import_asr_result(
    raw_response: Dict[str, Any],
    *,
    video_filename: str = "",
    audio_filename: str = "",
) -> AsrModel:
    """
    从 ASR 原始响应（asr-result.json）导入为 AsrModel。

    Args:
        raw_response: Doubao ASR 原始 JSON
        video_filename: 视频文件名（用于 media 元信息）
        audio_filename: 音频文件名（用于 media 元信息）

    Returns:
        AsrModel 实例
    """
    # 1. 提取 duration
    audio_info = raw_response.get("audio_info", {})
    result = raw_response.get("result", {})
    additions = result.get("additions", {})
    duration_ms = int(
        audio_info.get("duration", 0)
        or additions.get("duration", 0)
    )

    # 2. 解析 utterances（复用 doubao parser）
    utterances = parse_utterances(raw_response)

    # 3. 构建 segments
    segments = []
    for utt in utterances:
        segments.append(AsrSegment(
            id=_gen_seg_id(),
            start_ms=utt.start_ms,
            end_ms=utt.end_ms,
            text=utt.text,
            speaker=utt.speaker,
            emotion=utt.emotion or "neutral",
        ))

    # 4. 构建 AsrModel
    now = datetime.now(timezone.utc).isoformat()
    model = AsrModel(
        media=AsrMediaInfo(
            video=video_filename,
            audio=audio_filename,
            duration_ms=duration_ms,
        ),
        segments=segments,
        history=AsrHistory(
            rev=1,
            created_at=now,
            updated_at=now,
        ),
    )

    model.detect_overlaps()
    model.update_fingerprint()

    return model
