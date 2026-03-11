"""
TTS Processor: 语音合成（唯一对外入口）

职责：
- 接收 Phase 层的输入（dub_manifest）
- 通过 DB roles_map 解析声线分配
- 合成语音并返回 ProcessorResult（不负责文件 IO）

公共 API：
- run_per_segment(): Timeline-First，输出 per-segment WAVs
"""
from typing import Any, Dict, List, Optional

from .._types import ProcessorResult
from .volcengine import synthesize_tts_per_segment as synthesize_tts_per_segment_volcengine
from dubora_pipeline.schema.dub_manifest import DubManifest
from dubora_pipeline.schema.tts_report import TTSReport


def run_per_segment(
    dub_manifest: DubManifest,
    segments_dir: str,
    *,
    roles_map: Dict[str, str],
    # VolcEngine parameters
    volcengine_app_id: Optional[str] = None,
    volcengine_access_key: Optional[str] = None,
    volcengine_resource_id: str = "seed-tts-1.0",
    volcengine_format: str = "pcm",
    volcengine_sample_rate: int = 24000,
    # Common parameters
    language: str = "en-US",
    max_workers: int = 4,
    temp_dir: str,
) -> ProcessorResult:
    """
    Per-segment TTS synthesis (Timeline-First Architecture).

    Args:
        dub_manifest: DubManifest 对象
        segments_dir: 输出目录（per-segment WAVs）
        roles_map: {speaker_name: voice_type} from DB
    """
    from pathlib import Path

    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    # 1. Build voice assignment from roles_map (str-keyed for DubManifest.speaker matching)
    voice_assignment: Dict[str, Any] = {"speakers": {}}
    for role_id_str, voice_type in roles_map.items():
        if not voice_type:
            continue
        voice_assignment["speakers"][role_id_str] = {
            "voice_type": voice_type,
            "role_id": role_id_str,
        }

    # 2. Per-segment TTS synthesis
    if not volcengine_app_id or not volcengine_access_key:
        raise ValueError("VolcEngine TTS credentials not set")

    tts_report = synthesize_tts_per_segment_volcengine(
        dub_manifest=dub_manifest,
        voice_assignment=voice_assignment,
        segments_dir=segments_dir,
        temp_dir=str(temp_path),
        app_id=volcengine_app_id,
        access_key=volcengine_access_key,
        resource_id=volcengine_resource_id,
        format=volcengine_format,
        sample_rate=volcengine_sample_rate,
        language=language,
        max_workers=max_workers,
    )

    return ProcessorResult(
        outputs=[],
        data={
            "voice_assignment": voice_assignment,
            "tts_report": tts_report,
        },
        metrics={
            "total_segments": tts_report.total_segments,
            "success_count": tts_report.success_count,
            "failed_count": tts_report.failed_count,
        },
    )
