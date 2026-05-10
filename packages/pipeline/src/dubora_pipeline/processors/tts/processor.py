"""
TTS Processor: 语音合成（唯一对外入口）

职责：
- 接收 Phase 层的输入（dub_manifest）
- 通过 DB roles_map 解析声线分配
- 根据 tts_engine 全局配置选择合成引擎
- 合成语音并返回 ProcessorResult（不负责文件 IO）

公共 API：
- run_per_segment(): Timeline-First，输出 per-segment WAVs

内部模块：
- volcengine.py: VolcEngine TTS 实现
- fish.py: Fish Audio TTS 实现（声音克隆）
"""
from typing import Any, Dict, Optional

from .._types import ProcessorResult
from dubora_pipeline.schema.dub_manifest import DubManifest
from dubora_pipeline.schema.tts_report import TTSReport


def run_per_segment(
    dub_manifest: DubManifest,
    segments_dir: str,
    *,
    roles_map: Dict[str, str],
    tts_engine: str = "volcengine",
    # VolcEngine parameters
    volcengine_app_id: Optional[str] = None,
    volcengine_access_key: Optional[str] = None,
    volcengine_resource_id: str = "seed-tts-1.0",
    volcengine_format: str = "pcm",
    volcengine_sample_rate: int = 24000,
    # Fish Audio parameters
    fish_api_key: Optional[str] = None,
    # Common parameters
    language: str = "en-US",
    max_workers: int = 4,
    temp_dir: str,
    # Fish: sample audio map for voice cloning
    sample_audio_map: Optional[Dict[str, str]] = None,
) -> ProcessorResult:
    """
    Per-segment TTS synthesis (Timeline-First Architecture).

    Args:
        dub_manifest: DubManifest 对象
        segments_dir: 输出目录（per-segment WAVs）
        roles_map: {role_id_str: voice_type} from DB
        tts_engine: "volcengine" or "fish"
        sample_audio_map: {role_id_str: local_audio_path} (Fish 声音克隆用)
    """
    from pathlib import Path

    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    # Build voice assignment from roles_map
    voice_assignment: Dict[str, Any] = {"speakers": {}}
    for role_id_str, voice_type in roles_map.items():
        if not voice_type and tts_engine != "fish":
            continue
        entry: Dict[str, Any] = {
            "voice_type": voice_type,
            "role_id": role_id_str,
        }
        if sample_audio_map:
            entry["sample_audio_local"] = sample_audio_map.get(role_id_str, "")
        voice_assignment["speakers"][role_id_str] = entry

    if tts_engine == "fish":
        if not fish_api_key:
            raise ValueError("Fish Audio API key not set (FISH_API_KEY)")

        from .fish import synthesize_tts_per_segment as synthesize_fish
        tts_report = synthesize_fish(
            dub_manifest=dub_manifest,
            voice_assignment=voice_assignment,
            segments_dir=segments_dir,
            temp_dir=str(temp_path),
            api_key=fish_api_key,
            max_workers=max_workers,
        )
    else:
        if not volcengine_app_id or not volcengine_access_key:
            raise ValueError("VolcEngine TTS credentials not set")

        from .volcengine import synthesize_tts_per_segment as synthesize_volc
        tts_report = synthesize_volc(
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
