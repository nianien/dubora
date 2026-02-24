"""
Burn Phase: 烧录字幕到视频（只编排与IO，调用 ffmpeg）
"""
import os
import subprocess
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.utils.logger import info


class BurnPhase(Phase):
    """烧录字幕 Phase。"""
    
    name = "burn"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 mix.audio 和 subs.en_srt（或 subs.zh_srt）。"""
        return ["mix.audio", "subs.en_srt"]
    
    def provides(self) -> list[str]:
        """生成 burn.video。"""
        return ["burn.video"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Burn Phase。
        
        流程：
        1. 读取混音音频和字幕
        2. 使用 ffmpeg 将字幕烧录到视频
        """
        # 获取输入
        mix_audio_artifact = inputs["mix.audio"]
        mix_path = Path(ctx.workspace) / mix_audio_artifact.relpath
        
        en_srt_artifact = inputs["subs.en_srt"]
        srt_path = Path(ctx.workspace) / en_srt_artifact.relpath
        
        if not mix_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Mix audio not found: {mix_path}",
                ),
            )
        
        if not srt_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Subtitle file not found: {srt_path}",
                ),
            )
        
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
        
        # 输出视频路径（使用 runner 预分配的 outputs）
        output_video_path = outputs.get("burn.video")
        
        try:
            # 使用 ffmpeg 烧录字幕
            # 需要将 SRT 转换为 ASS（或直接使用 SRT）
            # 这里简化处理，直接使用 SRT
            
            # 转义路径（Windows 兼容）
            escaped_srt = os.path.abspath(srt_path).replace("\\", "\\\\").replace(":", "\\:")
            
            cmd = [
                "ffmpeg",
                "-i", str(video_path),
                "-i", str(mix_path),
                "-vf", f"subtitles={escaped_srt}",
                "-c:v", "libx264",
                "-c:a", "aac",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-y",
                str(output_video_path),
            ]
            
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            
            if not output_video_path.exists():
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message=f"Burn failed: {output_video_path} was not created",
                    ),
                )
            
            info(f"Burn completed: {output_video_path.name} (size: {output_video_path.stat().st_size / 1024 / 1024:.2f} MB)")
            
            # 返回 PhaseResult：只声明哪些 outputs 成功
            return PhaseResult(
                status="succeeded",
                outputs=["burn.video"],
                metrics={
                    "output_video_size_mb": output_video_path.stat().st_size / 1024 / 1024,
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
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )
