"""
Mix Processor: 音频混音（唯一对外入口）

职责：
- 接收 Phase 层的输入（tts_audio 或 segments + manifest）
- 调用内部实现进行混音
- 返回 ProcessorResult（不负责文件 IO）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在 impl.py
- Phase 层只调用 processor.run() 或 processor.run_timeline()

公共 API：
- run(): 旧版（使用拼接后的 TTS 音频）
- run_timeline(): 新版（Timeline-First Architecture）
"""
from typing import Optional

from .._types import ProcessorResult
from .impl import mix_audio, mix_timeline
from dubora.schema.dub_manifest import DubManifest
from dubora.schema.tts_report import TTSReport


def run(
    tts_path: str,
    video_path: str,
    *,
    accompaniment_path: Optional[str] = None,
    vocals_path: Optional[str] = None,
    mute_original: bool = False,
    mix_mode: str = "ducking",
    tts_volume: float = 1.0,
    accompaniment_volume: float = 0.8,
    vocals_volume: float = 0.15,
    duck_threshold: float = 0.05,
    duck_ratio: float = 10.0,
    duck_attack_ms: float = 20.0,
    duck_release_ms: float = 400.0,
    target_lufs: float = -16.0,
    true_peak: float = -1.0,
    output_path: str,
) -> ProcessorResult:
    """
    混音：将 TTS 音频和伴奏混合，输出到视频。
    
    Args:
        tts_path: TTS 音频文件路径
        video_path: 原始视频文件路径
        accompaniment_path: 伴奏音频文件路径（可选）
        target_lufs: 目标响度（LUFS）
        true_peak: 真峰值限制（dB）
        output_path: 输出视频文件路径
    
    Returns:
        ProcessorResult:
        - data.output_path: 输出视频文件路径
        - meta: 元数据
    """
    mix_audio(
        tts_path=tts_path,
        accompaniment_path=accompaniment_path,
        vocals_path=vocals_path,
        video_path=video_path,
        output_path=output_path,
        mute_original=mute_original,
        mix_mode=mix_mode,
        tts_volume=tts_volume,
        accompaniment_volume=accompaniment_volume,
        vocals_volume=vocals_volume,
        duck_threshold=duck_threshold,
        duck_ratio=duck_ratio,
        duck_attack_ms=duck_attack_ms,
        duck_release_ms=duck_release_ms,
        target_lufs=target_lufs,
        true_peak=true_peak,
    )
    
    return ProcessorResult(
        outputs=[],  # 由 Phase 声明 outputs，processor 只负责业务处理
        data={
            "output_path": output_path,
        },
        metrics={
            "target_lufs": target_lufs,
            "true_peak": true_peak,
        },
    )


def run_timeline(
    dub_manifest: DubManifest,
    tts_report: TTSReport,
    segments_dir: str,
    video_path: str,
    *,
    accompaniment_path: Optional[str] = None,
    vocals_path: Optional[str] = None,
    mute_original: bool = True,
    mix_mode: str = "ducking",
    tts_volume: float = 1.0,
    accompaniment_volume: float = 0.8,
    vocals_volume: float = 0.15,
    duck_threshold: float = 0.05,
    duck_ratio: float = 10.0,
    duck_attack_ms: float = 20.0,
    duck_release_ms: float = 400.0,
    target_lufs: float = -16.0,
    true_peak: float = -1.0,
    output_path: str,
    duration_tolerance_ms: int = 50,
) -> ProcessorResult:
    """
    Timeline-based mixing using adelay for segment placement.

    Uses dub_manifest for timing information and places each TTS segment
    at its correct start_ms position using FFmpeg's adelay filter.

    Args:
        dub_manifest: DubManifest object (SSOT for timing)
        tts_report: TTSReport object (segment info)
        segments_dir: Directory containing per-segment WAV files
        video_path: Original video file path
        accompaniment_path: Accompaniment audio path (optional)
        vocals_path: Original vocals path (optional)
        mute_original: Mute original dialogue (default True)
        mix_mode: Mix mode (ducking or simple)
        tts_volume: TTS volume multiplier
        accompaniment_volume: Accompaniment volume multiplier
        vocals_volume: Vocals volume multiplier
        duck_*: Ducking parameters
        target_lufs: Target loudness
        true_peak: True peak limit
        output_path: Output audio file path
        duration_tolerance_ms: Duration tolerance for validation

    Returns:
        ProcessorResult with actual_duration_ms in data
    """
    actual_duration_ms = mix_timeline(
        dub_manifest=dub_manifest,
        tts_report=tts_report,
        segments_dir=segments_dir,
        video_path=video_path,
        accompaniment_path=accompaniment_path,
        vocals_path=vocals_path,
        mute_original=mute_original,
        mix_mode=mix_mode,
        tts_volume=tts_volume,
        accompaniment_volume=accompaniment_volume,
        vocals_volume=vocals_volume,
        duck_threshold=duck_threshold,
        duck_ratio=duck_ratio,
        duck_attack_ms=duck_attack_ms,
        duck_release_ms=duck_release_ms,
        target_lufs=target_lufs,
        true_peak=true_peak,
        output_path=output_path,
    )

    return ProcessorResult(
        outputs=[],
        data={
            "output_path": output_path,
            "actual_duration_ms": actual_duration_ms,
            "expected_duration_ms": dub_manifest.audio_duration_ms,
        },
        metrics={
            "target_lufs": target_lufs,
            "true_peak": true_peak,
            "segments_count": len(tts_report.segments),
        },
    )
