"""
Sep Phase: 人声分离（使用本地 Demucs）

职责：把对白从 BGM/环境里"抠出来"，同时得到干净的背景轨用于混音
工具：Demucs htdemucs（推荐 v4 系列）

输入：
    • demux.audio (audio_raw.wav)

输出：
    • sep.vocals (audio/vocals.wav)
    • sep.accompaniment (audio/accompaniment.wav)

说明：Demucs 作为 v1 的"质量补丁"，会显著提升 ASR、diarization、混音观感。
"""
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.sep import run as sep_run
from dubora.utils.logger import info


class SepPhase(Phase):
    """人声分离 Phase。"""
    
    name = "sep"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 demux.audio。"""
        return ["demux.audio"]
    
    def provides(self) -> list[str]:
        """生成 sep.vocals 和 sep.accompaniment。"""
        return ["sep.vocals", "sep.accompaniment"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Sep Phase。
        
        从 demux.audio 分离人声和背景音乐。
        """
        # 获取输入
        audio_artifact = inputs["demux.audio"]
        audio_path = Path(ctx.workspace) / audio_artifact.relpath
        
        if not audio_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Audio file not found: {audio_path}",
                ),
            )
        
        if audio_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Audio file is empty: {audio_path}",
                ),
            )
        
        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("sep", {})
        model = phase_config.get("model", "htdemucs")
        
        info(f"Vocal separation: model={model}")
        info(f"Audio file: {audio_path.name} (size: {audio_path.stat().st_size / 1024 / 1024:.2f} MB)")
        
        # Phase 层负责文件 IO：使用 runner 预分配的 outputs.paths
        vocals_path = outputs.get("sep.vocals")
        accompaniment_path = outputs.get("sep.accompaniment")
        
        # 调用 Processor 层分离人声
        try:
            result = sep_run(
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
        
        # 验证输出文件
        if not vocals_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Vocal separation failed: {vocals_path} was not created",
                ),
            )
        
        if not accompaniment_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Vocal separation failed: {accompaniment_path} was not created",
                ),
            )
        
        if vocals_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Vocal separation failed: {vocals_path} is empty",
                ),
            )
        
        if accompaniment_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Vocal separation failed: {accompaniment_path} is empty",
                ),
            )
        
        vocals_size = vocals_path.stat().st_size / 1024 / 1024
        accompaniment_size = accompaniment_path.stat().st_size / 1024 / 1024

        info(f"Vocal separation succeeded:")
        info(f"  Vocals: {vocals_path.name} (size: {vocals_size:.2f} MB)")
        info(f"  Accompaniment: {accompaniment_path.name} (size: {accompaniment_size:.2f} MB)")

        # 返回 PhaseResult：只声明哪些 outputs 成功
        return PhaseResult(
            status="succeeded",
            outputs=["sep.vocals", "sep.accompaniment"],
            metrics={
                "vocals_size_mb": vocals_size,
                "accompaniment_size_mb": accompaniment_size,
            },
        )
