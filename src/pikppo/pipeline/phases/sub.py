"""
Sub Phase: 字幕后处理（从 ASR raw-response 生成字幕）

职责：
- 读取 ASR raw response（asr.asr_result，SSOT）
- 解析为 Utterance[]（使用 models/doubao/parser.py）
- 应用后处理策略（切句、speaker 处理等）
- 生成 Subtitle Model（subs.subtitle_model，SSOT）
- 生成字幕文件（subs.zh_srt，视图）

不负责：
- ASR 识别（由 ASR Phase 负责）
- 翻译（由 MT Phase 负责）

架构原则：
- 直接从 raw-response 生成（SSOT，包含完整语义信息）
- raw-response 是事实源，Subtitle Model 从事实源生成
"""
import json
from pathlib import Path
from typing import Dict

from pikppo.pipeline.core.phase import Phase
from pikppo.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from pikppo.pipeline.processors.srt import run as srt_run
from pikppo.utils.logger import info


class SubtitlePhase(Phase):
    """字幕后处理 Phase。"""
    
    name = "sub"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 asr.asr_result（SSOT：原始响应，包含完整语义信息）。"""
        return ["asr.asr_result"]
    
    def provides(self) -> list[str]:
        """生成 subs.subtitle_model (SSOT), subs.zh_srt (视图)。"""
        return ["subs.subtitle_model", "subs.zh_srt"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Subtitle Phase。
        
        流程：
        1. 读取 ASR raw response（asr.asr_result，SSOT）
        2. 解析为 Utterance[]（使用 models/doubao/parser.py）
        3. 应用后处理策略生成 Subtitle Model
        4. 生成 subtitle.model.json, zh.srt
        """
        # 获取输入（raw response，SSOT）
        asr_raw_response_artifact = inputs["asr.asr_result"]
        raw_response_path = Path(ctx.workspace) / asr_raw_response_artifact.relpath
        
        if not raw_response_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"ASR raw response file not found: {raw_response_path}",
                ),
            )
        
        # 读取 ASR raw response（SSOT）
        with open(raw_response_path, "r", encoding="utf-8") as f:
            raw_response = json.load(f)
        
        # 从 raw response 解析为 Utterance[]（使用 models/doubao/parser.py）
        try:
            from pikppo.models.doubao.parser import parse_utterances
            
            utterances = parse_utterances(raw_response)
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ParseError",
                    message=f"Failed to parse ASR raw response: {e}",
                ),
            )
        
        if not utterances:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="ASR raw response contains no utterances",
                ),
            )
        
        info(f"Parsed {len(utterances)} utterances from ASR raw response (SSOT)")
        
        # 获取配置
        workspace_path = Path(ctx.workspace)
        episode_stem = workspace_path.name
        
        phase_config = ctx.config.get("phases", {}).get("sub", {})
        postprofile = phase_config.get("postprofile", ctx.config.get("doubao_postprofile", "axis"))
        
        info(f"Subtitle strategy: postprofile={postprofile}")
        
        try:
            # 调用 Processor 层生成 Subtitle Model (SubtitleModel)
            # 直接从 raw_response 生成（SSOT，包含完整语义信息）
            result = srt_run(
                raw_response=raw_response,  # 主要输入：raw_response（SSOT）
                postprofile=postprofile,
            )
            
            # 从 ProcessorResult 提取 Subtitle Model (SSOT)
            subtitle_model = result.data["subtitle_model"]
            segments = result.data.get("segments", [])  # 向后兼容
            
            # 计算总 cues 数
            total_cues = sum(len(utt.cues) for utt in subtitle_model.utterances)
            info(f"Generated Subtitle Model v1.2 ({len(subtitle_model.utterances)} utterances, {total_cues} cues)")
            
            # Phase 层负责文件 IO：写入到 runner 预分配的 outputs.paths
            
            # 1. 保存 Subtitle Model JSON v1.2（SSOT）
            model_path = outputs.get("subs.subtitle_model")
            model_dict = {
                "schema": {
                    "name": subtitle_model.schema.name,
                    "version": subtitle_model.schema.version,
                },
                "audio": subtitle_model.audio,
                "utterances": [
                    {
                        "utt_id": utt.utt_id,
                        "speaker": utt.speaker,
                        "start_ms": utt.start_ms,
                        "end_ms": utt.end_ms,
                        "speech_rate": {
                            "zh_tps": utt.speech_rate.zh_tps,
                        },
                        "cues": [
                            {
                                "cue_id": cue.cue_id,
                                "start_ms": cue.start_ms,
                                "end_ms": cue.end_ms,
                                "speaker": cue.speaker,
                                "source": {
                                    "lang": cue.source.lang,
                                    "text": cue.source.text,
                                },
                                "emotion": {
                                    "label": cue.emotion.label,
                                    "confidence": cue.emotion.confidence,
                                    "intensity": cue.emotion.intensity,
                                } if cue.emotion else None,
                            }
                            for cue in utt.cues
                        ],
                    }
                    for utt in subtitle_model.utterances
                ],
            }
            model_path.parent.mkdir(parents=True, exist_ok=True)
            with open(model_path, "w", encoding="utf-8") as f:
                json.dump(model_dict, f, indent=2, ensure_ascii=False)
            info(f"Saved Subtitle Model (SSOT) to: {model_path}")
            
            # 2. 渲染 SRT 文件（Subtitle Model 的派生视图）
            from pikppo.pipeline.processors.srt.render_srt import render_srt
            # 从 Subtitle Model 的 utterances -> cues 转换为 Segment[]（用于 render_srt）
            # 使用 source.text（原文）作为 SRT 文本
            from pikppo.schema import Segment
            segments_for_srt = []
            for utt in subtitle_model.utterances:
                for cue in utt.cues:
                    segments_for_srt.append(Segment(
                        speaker=cue.speaker,
                        start_ms=cue.start_ms,
                        end_ms=cue.end_ms,
                        text=cue.source.text,  # 使用 source.text
                        emotion=cue.emotion.label if cue.emotion else None,
                        gender=None,  # v1.2 不再包含 gender
                    ))
            srt_path = outputs.get("subs.zh_srt")
            render_srt(segments_for_srt, srt_path)
            info(f"Rendered SRT file to: {srt_path}")
            
            # 返回 PhaseResult：只声明哪些 outputs 成功
            return PhaseResult(
                status="succeeded",
                outputs=[
                    "subs.subtitle_model",  # SSOT
                    "subs.zh_srt",          # 视图
                ],
                metrics={
                    "utterances_count": len(subtitle_model.utterances),
                    "cues_count": total_cues,
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
