"""
Extract Phase: 音频提取 + 人声分离（合并 demux + sep）

职责：
- 从视频提取音频（FFmpeg）
- 分离人声和伴奏（Demucs v4）

输入：
    （无，从 RunContext 获取 video_path）

输出：
    - extract.audio (audio_raw.wav)
    - extract.vocals (audio/vocals.wav)
    - extract.accompaniment (audio/accompaniment.wav)
"""
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.media import run as media_run
from dubora.pipeline.processors.sep import run as sep_run
from dubora.utils.logger import info


class ExtractPhase(Phase):
    """音频提取 + 人声分离 Phase。"""

    name = "extract"
    version = "1.0.0"

    def requires(self) -> list[str]:
        """第一个 phase，不需要上游 artifact。"""
        return []

    def provides(self) -> list[str]:
        """生成 extract.audio, extract.vocals, extract.accompaniment。"""
        return ["extract.audio", "extract.vocals", "extract.accompaniment"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Extract Phase。

        流程：
        1. 从 RunContext 获取 video_path，提取音频
        2. 从提取的音频分离人声和伴奏
        """
        # ── Step 1: 提取音频 ──────────────────────────────────────
        video_path = ctx.config.get("video_path")
        if not video_path:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="video_path not found in config",
                ),
            )

        video_file = Path(video_path)
        if not video_file.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Video file not found: {video_path}",
                ),
            )

        if video_file.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Video file is empty: {video_path}",
                ),
            )

        audio_path = outputs.get("extract.audio")

        try:
            media_run(
                video_path=str(video_path),
                output_path=str(audio_path),
            )
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )

        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Audio extraction failed: {audio_path}",
                ),
            )

        info(f"Audio extracted: {audio_path.name} (size: {audio_path.stat().st_size / 1024 / 1024:.2f} MB)")

        # ── Step 2: 人声分离 ──────────────────────────────────────
        phase_config = ctx.config.get("phases", {}).get("extract", {})
        model = phase_config.get("model", "htdemucs")

        info(f"Vocal separation: model={model}")

        vocals_path = outputs.get("extract.vocals")
        accompaniment_path = outputs.get("extract.accompaniment")

        try:
            sep_run(
                audio_path=str(audio_path),
                vocals_output_path=str(vocals_path),
                accompaniment_output_path=str(accompaniment_path),
                model=model,
            )
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )

        if not vocals_path.exists() or vocals_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Vocal separation failed: {vocals_path}",
                ),
            )

        if not accompaniment_path.exists() or accompaniment_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Vocal separation failed: {accompaniment_path}",
                ),
            )

        vocals_size = vocals_path.stat().st_size / 1024 / 1024
        accompaniment_size = accompaniment_path.stat().st_size / 1024 / 1024

        info(f"Vocal separation succeeded:")
        info(f"  Vocals: {vocals_path.name} (size: {vocals_size:.2f} MB)")
        info(f"  Accompaniment: {accompaniment_path.name} (size: {accompaniment_size:.2f} MB)")

        return PhaseResult(
            status="succeeded",
            outputs=["extract.audio", "extract.vocals", "extract.accompaniment"],
            metrics={
                "audio_size_mb": audio_path.stat().st_size / 1024 / 1024,
                "vocals_size_mb": vocals_size,
                "accompaniment_size_mb": accompaniment_size,
            },
        )
