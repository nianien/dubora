"""
Demux Phase: 从视频提取音频（只编排与IO，调用 pipeline.processors.media.extract_raw_audio）
"""
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.media import run as media_run
from dubora.utils.logger import info


class DemuxPhase(Phase):
    """音频提取 Phase。"""
    
    name = "demux"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 video 输入（从 RunContext 获取，不通过 manifest）。"""
        return []  # demux 是第一个 phase，不需要上游 artifact
    
    def provides(self) -> list[str]:
        """生成 demux.audio。"""
        return ["demux.audio"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Demux Phase。
        
        从 RunContext 获取 video_path，提取音频。
        """
        # 从 config 获取 video_path
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
        
        # Phase 层负责文件 IO：使用 runner 预分配的 outputs.paths
        audio_path = outputs.get("demux.audio")
        
        # 调用 Processor 层提取音频
        try:
            result = media_run(
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
        
        # 验证输出文件
        if not audio_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Audio extraction failed: {audio_path} was not created",
                ),
            )
        
        if audio_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Audio extraction failed: {audio_path} is empty",
                ),
            )
        
        info(f"Audio extracted: {audio_path.name} (size: {audio_path.stat().st_size / 1024 / 1024:.2f} MB)")
        
        # 返回 PhaseResult：只声明哪些 outputs 成功
        return PhaseResult(
            status="succeeded",
            outputs=["demux.audio"],
            metrics={
                "audio_size_mb": audio_path.stat().st_size / 1024 / 1024,
            },
        )
