"""
TTS Phase: 语音合成（只编排与IO，调用 pipeline.processors.tts.assign_voices 和 pipeline.processors.tts.synthesize_tts）
"""
import json
import shutil
from pathlib import Path
from typing import Dict

from pikppo.pipeline.core.phase import Phase
from pikppo.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from pikppo.pipeline.processors.tts import run as tts_run
from pikppo.models.voice_pool import DEFAULT_VOICE_POOL
from pikppo.utils.logger import info


class TTSPhase(Phase):
    """语音合成 Phase。"""
    
    name = "tts"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 subs.subtitle_model（SSOT）和 demux.audio（可选，用于声线分配）。"""
        return ["subs.subtitle_model", "demux.audio"]
    
    def provides(self) -> list[str]:
        """生成 tts.audio, tts.voice_assignment。"""
        return ["tts.audio", "tts.voice_assignment"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 TTS Phase。
        
        流程：
        1. 从 Subtitle Model 读取 cues（包含 target 翻译）
        2. 分配声线
        3. TTS 合成
        """
        # 获取输入（Subtitle Model SSOT）
        subtitle_model_artifact = inputs["subs.subtitle_model"]
        subtitle_model_path = Path(ctx.workspace) / subtitle_model_artifact.relpath
        
        if not subtitle_model_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Subtitle Model not found: {subtitle_model_path}",
                ),
            )
        
        audio_artifact = inputs.get("demux.audio")
        audio_path = None
        if audio_artifact:
            audio_path = Path(ctx.workspace) / audio_artifact.relpath
        
        workspace_path = Path(ctx.workspace)
        episode_stem = workspace_path.name
        
        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("tts", {})
        azure_key = phase_config.get("azure_key", ctx.config.get("azure_tts_key"))
        azure_region = phase_config.get("azure_region", ctx.config.get("azure_tts_region"))
        azure_language = phase_config.get("azure_language", ctx.config.get("azure_tts_language", "en-US"))
        max_workers = phase_config.get("max_workers", ctx.config.get("tts_max_workers", 4))
        voice_pool_path = phase_config.get("voice_pool_path", ctx.config.get("voice_pool_path"))
        
        if not azure_key or not azure_region:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="Azure TTS credentials not set (azure_key and azure_region required)",
                ),
            )
        
        try:
            # 读取 Subtitle Model v1.2
            with open(subtitle_model_path, "r", encoding="utf-8") as f:
                model_data = json.load(f)
            
            # v1.2: 从 utterances 中提取所有 cues
            utterances = model_data.get("utterances", [])
            cues = []
            for utt in utterances:
                cues.extend(utt.get("cues", []))
            
            if not cues:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="ValueError",
                        message="No cues found in Subtitle Model",
                    ),
                )
            
            # v1.2: 从 translate.context.json 读取翻译结果（不写回 SSOT）
            translate_context_path = workspace_path / "translate.context.json"
            translations_map = {}
            if translate_context_path.exists():
                with open(translate_context_path, "r", encoding="utf-8") as f:
                    context_data = json.load(f)
                    for trans in context_data.get("translations", []):
                        translations_map[trans.get("cue_id", "")] = trans
            
            # 从 cues 和 translations 提取 segments（用于 TTS）
            # 只使用有翻译的 cues
            segments = []
            for cue in cues:
                cue_id = cue.get("cue_id", "")
                translation = translations_map.get(cue_id)
                
                if not translation or not translation.get("text"):
                    continue  # 跳过未翻译的 cue
                
                segments.append({
                    "id": cue_id,
                    "start": cue.get("start_ms", 0) / 1000.0,  # 毫秒转秒
                    "end": cue.get("end_ms", 0) / 1000.0,
                    "text": translation.get("text", ""),  # 使用翻译文本（英文）
                    "speaker": cue.get("speaker", ""),
                    "emotion": cue.get("emotion", {}).get("label") if cue.get("emotion") else None,
                })
            
            # 准备 voice pool
            if not voice_pool_path:
                temp_voices_dir = workspace_path / ".temp_voices"
                temp_voices_dir.mkdir(exist_ok=True)
                pool_file = temp_voices_dir / "voice_pool.json"
                with open(pool_file, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_VOICE_POOL, f, indent=2, ensure_ascii=False)
                voice_pool_path = str(pool_file)
            
            # 调用 Processor 层（分配声线 + TTS 合成）
            temp_dir = str(workspace_path / ".temp_tts")
            Path(temp_dir).mkdir(exist_ok=True)
            
            result = tts_run(
                segments=segments,
                reference_audio_path=str(audio_path) if audio_path and audio_path.exists() else None,
                voice_pool_path=voice_pool_path,
                azure_key=azure_key,
                azure_region=azure_region,
                language=azure_language,
                max_workers=max_workers,
                temp_dir=temp_dir,
            )
            
            # 从 ProcessorResult 提取数据
            voice_assignment = result.data["voice_assignment"]
            audio_path_temp = result.data["audio_path"]
            
            # Phase 层负责文件 IO：移动到最终位置
            voice_assignment_path = workspace_path / "voice-assignment.json"
            with open(voice_assignment_path, "w", encoding="utf-8") as f:
                json.dump(voice_assignment, f, indent=2, ensure_ascii=False)
            
            audio_dir = workspace_path / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            tts_path = audio_dir / "tts.wav"
            
            if Path(audio_path_temp).exists():
                shutil.move(audio_path_temp, tts_path)
            
            # 清理临时目录
            if Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            
            if not tts_path.exists():
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message=f"TTS synthesis failed: {tts_path} was not created",
                    ),
                )
            
            info(f"TTS synthesis completed: {tts_path.name}")
            
            # 返回 PhaseResult：只声明哪些 outputs 成功
            return PhaseResult(
                status="succeeded",
                outputs=[
                    "tts.audio",
                    "tts.voice_assignment",
                ],
                metrics={
                    "tts_audio_size_mb": tts_path.stat().st_size / 1024 / 1024,
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
