"""
ASR Phase: 语音识别（只做识别，不负责字幕后处理）

职责：
- 读取音频文件
- 上传到 TOS（如果需要）
- 调用 ASR API
- 保存原始 ASR 响应（asr.asr_result，SSOT）

产出设计：
- asr.asr_result：SSOT（原始响应，包含完整语义信息，emotion/gender/score/degree）

不负责：
- 字幕后处理（由 Subtitle Phase 负责）
- 切句策略（由 Subtitle Phase 负责）

架构原则：
- raw-response 是 SSOT，直接从 raw-response 生成 Subtitle Model
- pipeline 默认走 IR（稳定、可替换、可复用）
"""
import json
from pathlib import Path
from typing import Dict

from pikppo.pipeline.core.phase import Phase
from pikppo.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from pikppo.pipeline.processors.asr import run as asr_run
from pikppo.infra.storage.tos import TosStorage
from pikppo.utils.logger import info


class ASRPhase(Phase):
    """语音识别 Phase（只做识别，不负责字幕后处理）。"""
    
    name = "asr"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 demux.audio。"""
        return ["demux.audio"]
    
    def provides(self) -> list[str]:
        """生成 asr.asr_result（SSOT：原始响应，包含完整语义信息）。"""
        return ["asr.asr_result"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 ASR Phase。
        
        流程：
        1. 读取音频文件
        2. 上传到 TOS（如果需要）
        3. 调用 ASR API
        4. 解析为 IR（Utterance[]）
        5. 保存 IR 和 raw response
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
        phase_config = ctx.config.get("phases", {}).get("asr", {})
        preset = phase_config.get("preset", ctx.config.get("doubao_asr_preset", "asr_vad_spk"))
        hotwords = phase_config.get("hotwords", ctx.config.get("doubao_hotwords"))
        
        info(f"ASR strategy: preset={preset}")
        info(f"Audio file: {audio_path.name} (size: {audio_path.stat().st_size / 1024 / 1024:.2f} MB)")
        
        try:
            # 1. 获取音频 URL（上传到 TOS 如果需要）
            audio_url = ctx.config.get("doubao_audio_url")
            if not audio_url:
                # 如果是 URL 直接使用，否则上传到 TOS
                audio_path_str = str(audio_path)
                if audio_path_str.startswith(("http://", "https://")):
                    audio_url = audio_path_str
                else:
                    # 从 video_path 提取系列名
                    video_path = ctx.config.get("video_path", "")
                    series = None
                    if video_path:
                        video_path_obj = Path(video_path)
                        if len(video_path_obj.parts) >= 2:
                            parts = video_path_obj.parts
                            if "videos" in parts:
                                idx = parts.index("videos")
                                if idx + 1 < len(parts):
                                    series = parts[idx + 1]
                    
                    storage = TosStorage()
                    audio_url = storage.upload(audio_path, prefix=series)
            
            # 2. 调用 Processor 层进行 ASR
            result = asr_run(
                audio_url=audio_url,
                preset=preset,
                hotwords=hotwords,
            )
            
            # 从 ProcessorResult 提取数据
            raw_response = result.data["raw_response"]
            utterances = result.data["utterances"]
            
            if not utterances:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message="ASR produced no utterances",
                    ),
                )
            
            info(f"ASR succeeded ({len(utterances)} utterances)")
            
            # 3. Phase 层负责文件 IO：写入到 runner 预分配的 outputs.paths
            # 只保存 raw response（SSOT，包含完整语义信息）
            raw_response_path = outputs.get("asr.asr_result")
            raw_response_path.parent.mkdir(parents=True, exist_ok=True)
            with open(raw_response_path, "w", encoding="utf-8") as f:
                json.dump(raw_response, f, indent=2, ensure_ascii=False)
            
            info(f"Saved raw ASR response (SSOT) to: {raw_response_path}")
            
            # 返回 PhaseResult：只声明哪些 outputs 成功
            return PhaseResult(
                status="succeeded",
                outputs=[
                    "asr.asr_result",  # 只生成 asr-result.json
                ],
                metrics={
                    "utterances_count": len(utterances),
                },
            )
            
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )
