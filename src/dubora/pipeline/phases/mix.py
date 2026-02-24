"""
Mix Phase: 混音 (Timeline-First Architecture)

输入:
  - dub.dub_manifest: Timeline SSOT (source/dub.model.json)
  - tts.segments_dir: Per-segment WAV files
  - tts.report: TTS synthesis report

输出:
  - mix.audio: Final mixed audio (exact duration matching original)

使用 adelay filter 进行 timeline placement，不再依赖 TTS 的拼接输出。
"""
import subprocess
from pathlib import Path
from typing import Dict, Optional

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.mix import run_timeline as mix_run_timeline
from dubora.schema.dub_manifest import dub_manifest_from_dict
from dubora.schema.tts_report import tts_report_from_dict
from dubora.utils.logger import info, warning
import json


class MixPhase(Phase):
    """混音 Phase (Timeline-First Architecture)。"""

    name = "mix"
    version = "2.0.0"

    def requires(self) -> list[str]:
        """需要 dub_manifest, segments_dir, report, 和可选的 sep outputs。"""
        return ["dub.dub_manifest", "tts.segments_dir", "tts.report"]

    def provides(self) -> list[str]:
        """生成 mix.audio。"""
        return ["mix.audio"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Mix Phase (Timeline-First Architecture)。

        流程：
        1. 读取 dub.model.json (timeline SSOT)
        2. 读取 tts_report.json (per-segment info)
        3. 使用 adelay 进行 timeline placement
        4. 混合 BGM + TTS
        5. 使用 apad + atrim 强制精确时长
        6. 校验输出时长
        """
        workspace_path = Path(ctx.workspace)

        # 获取 dub_manifest
        dub_manifest_artifact = inputs["dub.dub_manifest"]
        dub_manifest_path = workspace_path / dub_manifest_artifact.relpath
        if not dub_manifest_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Dub manifest not found: {dub_manifest_path}",
                ),
            )

        # 获取 segments_dir
        segments_dir_artifact = inputs["tts.segments_dir"]
        segments_dir = workspace_path / segments_dir_artifact.relpath
        if not segments_dir.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"TTS segments directory not found: {segments_dir}",
                ),
            )

        # 获取 tts_report
        tts_report_artifact = inputs["tts.report"]
        tts_report_path = workspace_path / tts_report_artifact.relpath
        if not tts_report_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"TTS report not found: {tts_report_path}",
                ),
            )

        # Load manifest and report
        with open(dub_manifest_path, "r", encoding="utf-8") as f:
            dub_manifest = dub_manifest_from_dict(json.load(f))

        with open(tts_report_path, "r", encoding="utf-8") as f:
            tts_report = tts_report_from_dict(json.load(f))

        info(f"Loaded dub manifest: {len(dub_manifest.utterances)} utterances, audio_duration_ms={dub_manifest.audio_duration_ms}")
        info(f"Loaded TTS report: {tts_report.success_count}/{tts_report.total_segments} succeeded")

        # 获取 accompaniment（可选，从 manifest 手动查找）
        accompaniment_path = None
        vocals_path = None
        try:
            from dubora.pipeline.core.manifest import Manifest
            manifest_path = workspace_path / "manifest.json"
            manifest = Manifest(manifest_path)
            accompaniment_artifact = manifest.get_artifact("sep.accompaniment", required_by=None)
            accompaniment_path = workspace_path / accompaniment_artifact.relpath
            if not accompaniment_path.exists():
                accompaniment_path = None

            vocals_artifact = manifest.get_artifact("sep.vocals", required_by=None)
            vocals_path = workspace_path / vocals_artifact.relpath
            if not vocals_path.exists():
                vocals_path = None
        except (ValueError, FileNotFoundError):
            accompaniment_path = None
            vocals_path = None

        # 获取 video_path（从 config）
        video_path = ctx.config.get("video_path")
        if not video_path:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="video_path not found in config",
                ),
            )

        # 使用预分配的输出路径
        mix_path = outputs.get("mix.audio")
        mix_path.parent.mkdir(parents=True, exist_ok=True)

        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("mix", {})
        target_lufs = phase_config.get("target_lufs", ctx.config.get("dub_target_lufs", -16.0))
        true_peak = phase_config.get("true_peak", ctx.config.get("dub_true_peak", -1.0))
        mute_original = bool(phase_config.get("mute_original", True))
        mix_mode = phase_config.get("mode", "ducking")
        tts_volume = float(phase_config.get("tts_volume", 1.0))
        accompaniment_volume = float(phase_config.get("accompaniment_volume", 0.8))
        vocals_volume = float(phase_config.get("vocals_volume", 0.15))
        duck_threshold = float(phase_config.get("duck_threshold", 0.05))
        duck_ratio = float(phase_config.get("duck_ratio", 10.0))
        duck_attack_ms = float(phase_config.get("duck_attack_ms", 20.0))
        duck_release_ms = float(phase_config.get("duck_release_ms", 400.0))
        duration_tolerance_ms = int(phase_config.get("duration_tolerance_ms", 50))

        try:
            result = mix_run_timeline(
                dub_manifest=dub_manifest,
                tts_report=tts_report,
                segments_dir=str(segments_dir),
                video_path=video_path,
                accompaniment_path=str(accompaniment_path) if accompaniment_path else None,
                vocals_path=str(vocals_path) if vocals_path else None,
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
                output_path=str(mix_path),
                duration_tolerance_ms=duration_tolerance_ms,
            )

            if not mix_path.exists():
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message=f"Mix failed: {mix_path} was not created",
                    ),
                )

            # Validate duration
            actual_ms = result.data.get("actual_duration_ms", 0)
            expected_ms = dub_manifest.audio_duration_ms
            duration_diff = abs(actual_ms - expected_ms)

            if duration_diff > duration_tolerance_ms:
                warning(
                    f"Duration mismatch: expected {expected_ms}ms, got {actual_ms}ms "
                    f"(diff: {duration_diff}ms > tolerance: {duration_tolerance_ms}ms)"
                )

            info(f"Mix completed: {mix_path.name} (size: {mix_path.stat().st_size / 1024 / 1024:.2f} MB)")
            info(f"Duration: {actual_ms}ms (expected: {expected_ms}ms, diff: {duration_diff}ms)")

            return PhaseResult(
                status="succeeded",
                outputs=["mix.audio"],
                metrics={
                    "mix_audio_size_mb": mix_path.stat().st_size / 1024 / 1024,
                    "expected_duration_ms": expected_ms,
                    "actual_duration_ms": actual_ms,
                    "duration_diff_ms": duration_diff,
                },
            )

        except subprocess.CalledProcessError as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"FFmpeg failed: {e.stderr or e.stdout or 'Unknown error'}",
                ),
            )
        except Exception as e:
            import traceback
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                    traceback=traceback.format_exc(),
                ),
            )
