"""
TTS Processor: 语音合成（唯一对外入口）

职责：
- 接收 Phase 层的输入（dub_manifest）
- 通过 role_speakers.json 解析声线分配
- 合成语音并返回 ProcessorResult（不负责文件 IO）

声线解析链路（单文件 role_speakers.json）：
  speakers: pa → "PingAn"
  roles:    "PingAn" → voice_type
  未标注 → default_roles[gender] → voice_type

公共 API：
- run_per_segment(): Timeline-First，输出 per-segment WAVs
"""
from typing import Any, Dict, List, Optional

from .._types import ProcessorResult
from .volcengine import synthesize_tts_per_segment as synthesize_tts_per_segment_volcengine
from dubora.pipeline.processors.voiceprint.speaker_to_role import resolve_voice_assignments
from dubora.schema.dub_manifest import DubManifest
from dubora.schema.tts_report import TTSReport


def run_per_segment(
    dub_manifest: DubManifest,
    segments_dir: str,
    *,
    role_speakers_path: Optional[str] = None,
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
        role_speakers_path: role_speakers.json 路径
    """
    from pathlib import Path

    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    # 1. 解析声线分配
    if not role_speakers_path or not Path(role_speakers_path).exists():
        raise FileNotFoundError(
            f"role_speakers.json not found: {role_speakers_path}. "
            "Run sub phase first, then configure speakers/roles in role_speakers.json."
        )

    speaker_genders: Dict[str, str] = {}
    for utt in dub_manifest.utterances:
        spk = utt.speaker
        if spk and spk not in speaker_genders:
            speaker_genders[spk] = utt.gender or "unknown"

    role_map = resolve_voice_assignments(
        role_speakers_path,
        speaker_genders=speaker_genders,
    )
    voice_assignment = {"speakers": {}}
    for spk, info_dict in role_map.items():
        voice_assignment["speakers"][spk] = {
            "voice_type": info_dict["voice_type"],
            "role_id": info_dict.get("role_id", ""),
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
