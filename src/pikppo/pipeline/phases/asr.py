"""
ASR Phase: 语音识别（只做识别，不负责字幕后处理）

职责：
- 读取音频文件
- 上传到 TOS（如果需要）
- 调用 ASR API
- 产出 IR（中间表示）：Utterance[] / Word[]
- 保存原始 ASR 响应（asr.raw_response，可选，用于调试/复现）

产出设计：
- asr.result：IR（Utterance[]，稳定、可替换、可复用）
- asr.raw_response：raw（可选，用于调试/复现）

不负责：
- 字幕后处理（由 Subtitle Phase 负责）
- 切句策略（由 Subtitle Phase 负责）

架构原则：
- raw 是日志/证据，不是接口契约
- pipeline 默认走 IR（稳定、可替换、可复用）
"""
import json
import os
from pathlib import Path
from typing import Dict

from pikppo.pipeline.core.phase import Phase
from pikppo.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext
from pikppo.pipeline.processors.asr import transcribe
from pikppo.models.doubao import parse_utterances
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
        """生成 asr.result（IR：Utterance[]）和 asr.raw_response（可选，用于调试）。"""
        return ["asr.result", "asr.raw_response"]
    
    def run(self, ctx: RunContext, inputs: Dict[str, Artifact]) -> PhaseResult:
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
        audio_path = Path(ctx.workspace) / audio_artifact.path
        
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
        
        # 获取 episode stem
        workspace_path = Path(ctx.workspace)
        episode_stem = workspace_path.name
        
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
            
            # 2. 调用 ASR
            raw_response, utterances = transcribe(
                audio_url=audio_url,
                preset=preset,
                hotwords=hotwords,
            )
            
            if not utterances:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message="ASR produced no utterances",
                    ),
                )
            
            info(f"ASR succeeded ({len(utterances)} utterances)")
            
            # 3. 保存 IR（中间表示）
            asr_dir = workspace_path / "asr"
            asr_dir.mkdir(parents=True, exist_ok=True)
            
            # 保存 IR：将 Utterance[] 序列化为 JSON
            result_path = asr_dir / "result.json"
            result_data = {
                "utterances": [
                    {
                        "speaker": utt.speaker,
                        "start_ms": utt.start_ms,
                        "end_ms": utt.end_ms,
                        "text": utt.text,
                        "words": [
                            {
                                "start_ms": w.start_ms,
                                "end_ms": w.end_ms,
                                "text": w.text,
                                "speaker": w.speaker,
                            }
                            for w in (utt.words or [])
                        ] if utt.words else None,
                    }
                    for utt in utterances
                ],
            }
            
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result_data, f, indent=2, ensure_ascii=False)
            
            info(f"Saved ASR IR to: {result_path}")
            
            # 4. 保存 raw response（可选，用于调试/复现）
            raw_response_path = asr_dir / "raw-response.json"
            with open(raw_response_path, "w", encoding="utf-8") as f:
                json.dump(raw_response, f, indent=2, ensure_ascii=False)
            
            info(f"Saved raw ASR response to: {raw_response_path}")
            
            # 返回 artifacts
            return PhaseResult(
                status="succeeded",
                artifacts={
                    "asr.result": Artifact(
                        key="asr.result",
                        path="asr/result.json",
                        kind="json",
                        fingerprint="",  # runner 会计算
                    ),
                    "asr.raw_response": Artifact(
                        key="asr.raw_response",
                        path="asr/raw-response.json",
                        kind="json",
                        fingerprint="",  # runner 会计算
                    ),
                },
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
