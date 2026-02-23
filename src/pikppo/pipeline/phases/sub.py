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
from typing import Dict, Any, Optional, List

from pikppo.pipeline.core.phase import Phase
from pikppo.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from pikppo.pipeline.processors.srt import run as srt_run
from pikppo.schema.asr_fix import AsrFix
from pikppo.utils.logger import info


def _get_utterance_text_from_raw_response(
    utt: "SubtitleUtterance",
    raw_utterances: List[Dict[str, Any]],
) -> str:
    """
    从 raw_response 的 utterances 中获取对应 utterance 的 text。
    
    通过匹配 start_ms 和 end_ms 来找到对应的 raw utterance。
    """
    for raw_utt in raw_utterances:
        raw_start = int(raw_utt.get("start_time", 0))
        raw_end = int(raw_utt.get("end_time", raw_start))
        # 匹配时间范围（允许小的误差）
        if abs(raw_start - utt.start_ms) <= 10 and abs(raw_end - utt.end_ms) <= 10:
            text = str(raw_utt.get("text", "")).strip()
            if text:
                return text
    
    # 如果找不到，回退到合并 cues 的文本
    return "".join(cue.source.text for cue in utt.cues)


class SubtitlePhase(Phase):
    """字幕后处理 Phase。"""
    
    name = "sub"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 asr.asr_result（word 级时间轴）。

        注意：asr.fix.json 从磁盘直接读取（不通过 manifest），
        这样人工编辑 fix.json 不会触发 ASR 重跑。
        """
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

        # 读取 ASR fix（人工校准层，从磁盘直接读取）
        # asr.fix.json 不在 manifest 中，直接从约定路径读取
        asr_fix: Optional[AsrFix] = None
        asr_fix_path = Path(ctx.workspace) / "source" / "asr.fix.json"
        if asr_fix_path.exists():
            with open(asr_fix_path, "r", encoding="utf-8") as f:
                asr_fix_data = json.load(f)
            asr_fix = AsrFix.from_dict(asr_fix_data)
            info(f"Loaded ASR fix (human-editable) with {len(asr_fix.utterances)} utterances")
        
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
        
        # 获取 Utterance Normalization 配置（从 PipelineConfig）
        # 这些参数控制如何从 word-level timestamps 重建视觉友好的 utterance 边界
        utt_norm_config = {
            "silence_split_threshold_ms": ctx.config.get(
                "utt_norm_silence_split_threshold_ms", 450
            ),
            "min_utterance_duration_ms": ctx.config.get(
                "utt_norm_min_duration_ms", 900
            ),
            "max_utterance_duration_ms": ctx.config.get(
                "utt_norm_max_duration_ms", 8000
            ),
            "trailing_silence_cap_ms": ctx.config.get(
                "utt_norm_trailing_silence_cap_ms", 350
            ),
            "keep_gap_as_field": ctx.config.get(
                "utt_norm_keep_gap_as_field", True
            ),
        }

        info(f"Subtitle strategy: postprofile={postprofile}")
        info(f"Utterance Normalization: silence_split={utt_norm_config['silence_split_threshold_ms']}ms, "
             f"min_dur={utt_norm_config['min_utterance_duration_ms']}ms, "
             f"max_dur={utt_norm_config['max_utterance_duration_ms']}ms")

        try:
            # 从 raw_response 中提取音频时长
            audio_duration_ms = None
            audio_info = raw_response.get("audio_info") or {}
            if audio_info.get("duration"):
                audio_duration_ms = int(audio_info["duration"])

            # 调用 Processor 层生成 Subtitle Model (SubtitleModel)
            # 使用 Utterance Normalization 重建视觉友好的 utterance 边界
            result = srt_run(
                raw_response=raw_response,  # 主要输入：raw_response（word 时间轴）
                asr_fix=asr_fix,             # 人工校准层（speaker/text）
                postprofile=postprofile,
                audio_duration_ms=audio_duration_ms,
                # Utterance Normalization 配置
                **utt_norm_config,
            )
            
            # 从 ProcessorResult 提取 Subtitle Model (SSOT)
            subtitle_model = result.data["subtitle_model"]
            segments = result.data.get("segments", [])  # 向后兼容
            
            # 计算总 cues 数
            total_cues = sum(len(utt.cues) for utt in subtitle_model.utterances)
            info(f"Generated Subtitle Model v1.3 ({len(subtitle_model.utterances)} utterances, {total_cues} cues)")
            
            # Phase 层负责文件 IO：写入到 runner 预分配的 outputs.paths
            
            # 1. 保存 Subtitle Model JSON v1.2（SSOT）
            # 从 raw_response 中提取 utterance 信息（用于序列化时获取 text 和 gender）
            result = raw_response.get("result") or {}
            raw_utterances = result.get("utterances") or []
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
                        "speaker": {
                            "id": utt.speaker.id,
                            "gender": utt.speaker.gender,
                            "speech_rate": {
                                "zh_tps": utt.speaker.speech_rate.zh_tps,
                            },
                            "emotion": {
                                "label": utt.speaker.emotion.label,
                                "confidence": utt.speaker.emotion.confidence,
                                "intensity": utt.speaker.emotion.intensity,
                            } if utt.speaker.emotion else None,
                        },
                        "start_ms": utt.start_ms,  # 取自 asr-result.json
                        "end_ms": utt.end_ms,  # 取自 asr-result.json
                        # utterance 维度的 text 字段：以 cues 为准（SSOT 自洽）
                        # 重要：SSOT 生成阶段可能会按"超长停顿"拆分 utterance，因此不能再依赖 raw_utterance 精确匹配。
                        "text": "".join(cue.source.text for cue in utt.cues),
                        "cues": [
                            {
                                "start_ms": cue.start_ms,
                                "end_ms": cue.end_ms,
                                "source": {
                                    "lang": cue.source.lang,
                                    "text": cue.source.text,
                                },
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
                        speaker=utt.speaker.id,  # 使用 utterance 级别的 speaker id
                        start_ms=cue.start_ms,
                        end_ms=cue.end_ms,
                        text=cue.source.text,  # 使用 source.text
                        emotion=utt.speaker.emotion.label if utt.speaker.emotion else None,
                        gender=utt.speaker.gender,
                    ))
            srt_path = outputs.get("subs.zh_srt")
            render_srt(segments_for_srt, srt_path)
            info(f"Rendered SRT file to: {srt_path}")
            
            # Side-effect: 追加新 speaker 到 role_speakers.json（剧级文件）
            try:
                unique_speakers = list(dict.fromkeys(
                    utt_dict["speaker"]["id"]
                    for utt_dict in model_dict["utterances"]
                    if utt_dict.get("speaker", {}).get("id")
                ))
                if unique_speakers:
                    from pikppo.pipeline.processors.voiceprint.speaker_to_role import update_speakers
                    dict_dir = workspace_path.parent / "dict"
                    rc_path = str(dict_dir / "role_speakers.json")
                    episode_id = workspace_path.name
                    update_speakers(unique_speakers, rc_path, episode_id)
            except Exception as e:
                info(f"Warning: failed to update role_speakers.json: {e}")

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
