"""
Mix Phase: 混音 (Timeline-First Architecture)

输入:
  - extract.audio: 原始音频（用于 probe duration）
  - tts.segments_dir: Per-segment WAV files
  - enriched utterances from DB

输出:
  - mix.audio: Final mixed audio (exact duration matching original)

使用 adelay filter 进行 timeline placement，不再依赖 TTS 的拼接输出。
"""
import subprocess
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.mix import run_timeline as mix_run_timeline
from dubora.schema.dub_manifest import dub_manifest_from_utterances
from dubora.utils.logger import info, warning


def _probe_duration_ms(audio_path: str) -> int:
    """Probe audio duration using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        raise RuntimeError(f"ffprobe returned invalid duration for {audio_path}")
    return int(float(duration_str) * 1000)


class MixPhase(Phase):
    """混音 Phase (Timeline-First Architecture)。"""

    name = "mix"
    version = "3.0.0"

    def requires(self) -> list[str]:
        """需要 extract.audio (duration probe), segments_dir。TTS 结果从 DB 读取。"""
        return ["extract.audio", "tts.segments_dir"]

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
        1. 从 DB 构建 DubManifest（enriched utterances）
        2. 使用 adelay 进行 timeline placement
        3. 混合 BGM + TTS
        4. 使用 apad + atrim 强制精确时长
        5. 校验输出时长
        """
        workspace_path = Path(ctx.workspace)
        store = ctx.store
        episode_id = ctx.episode_id

        if not store or not episode_id:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="Mix requires DB store and episode_id. Ensure pipeline is running in DB mode.",
                ),
            )

        # Probe audio duration from extract.audio
        audio_artifact = inputs.get("extract.audio")
        audio_duration_ms = 0
        if audio_artifact:
            audio_path = workspace_path / audio_artifact.relpath
            if audio_path.exists():
                try:
                    audio_duration_ms = _probe_duration_ms(str(audio_path))
                    info(f"Probed audio duration: {audio_duration_ms}ms")
                except RuntimeError as e:
                    warning(f"Could not probe audio duration: {e}")

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

        # Build DubManifest from succeeded enriched utterances
        all_utts = store.get_utterances(episode_id)
        succeeded_utts = [u for u in all_utts if not u.get("tts_error")]
        dub_manifest = dub_manifest_from_utterances(succeeded_utts, audio_duration_ms)

        # Extract singing segments from SRC cues (keep original vocals for these time windows)
        src_cues = store.get_cues(episode_id)
        singing_segments = [
            (c["start_ms"], c["end_ms"])
            for c in src_cues
            if c.get("kind") == "singing"
        ]

        info(f"Loaded {len(all_utts)} utterances from DB, {len(succeeded_utts)} succeeded, {len(dub_manifest.utterances)} with translations")
        if singing_segments:
            info(f"Found {len(singing_segments)} singing segments to preserve")

        # 获取 accompaniment / vocals（可选，路径动态计算）
        from dubora.pipeline.core.manifest import resolve_artifact_path
        accompaniment_path = None
        vocals_path = None
        p = resolve_artifact_path("extract.accompaniment", workspace_path)
        if p.exists():
            accompaniment_path = p
        p = resolve_artifact_path("extract.vocals", workspace_path)
        if p.exists():
            vocals_path = p

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
                tts_report=None,
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
                singing_segments=singing_segments,
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
