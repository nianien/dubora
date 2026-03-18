"""
Parse Phase: 字幕后处理（从 ASR raw-response 生成 DB cues）

职责：
- 读取 ASR raw response（asr.asr_result，SSOT）
- 解析为 Utterance[]（使用 models/doubao/parser.py）
- 应用后处理策略（切句、speaker 处理等）
- 执行 reseg + emotion_correct
- 写入 DB SRC cues（唯一输出）

不负责：
- ASR 识别（由 ASR Phase 负责）
- 翻译（由 MT Phase 负责）
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_pipeline.processors.srt import run as srt_run
from dubora_pipeline.schema.asr_model import AsrModel, AsrSegment, AsrMediaInfo, AsrHistory
from dubora_core.config import resolve_emotion
from dubora_core.utils.logger import info, warning


class ParsePhase(Phase):
    """字幕后处理 Phase。"""

    name = "parse"
    version = "2.0.0"

    def requires(self) -> list[str]:
        """需要 asr.asr_result（word 级时间轴）。"""
        return ["asr.asr_result"]

    def provides(self) -> list[str]:
        """DB cues 是唯一输出，不再声明文件产物。"""
        return []

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Parse Phase。

        流程：
        1. 读取 ASR raw response（asr.asr_result，SSOT）
        2. 解析为 Utterance[]（使用 models/doubao/parser.py）
        3. 应用后处理策略生成 SubtitleModel
        4. 转换为 AsrModel 格式，经 reseg + emotion 修正后写入 DB cues
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
            from dubora_pipeline.models.doubao.parser import parse_utterances

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
        phase_config = ctx.config.get("phases", {}).get("parse", {})
        postprofile = phase_config.get("postprofile", ctx.config.get("doubao_postprofile", "axis"))

        # 获取 Utterance Normalization 配置
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

            # 调用 Processor 层生成 Subtitle Model
            result = srt_run(
                raw_response=raw_response,
                postprofile=postprofile,
                audio_duration_ms=audio_duration_ms,
                **utt_norm_config,
            )

            subtitle_model = result.data["subtitle_model"]

            total_cues = sum(len(utt.cues) for utt in subtitle_model.utterances)
            info(f"Generated Subtitle Model ({len(subtitle_model.utterances)} utterances, {total_cues} cues)")

            # 将 SubtitleModel 转换为 AsrModel 格式（内存中，最终写 DB）
            segments = []
            for utt in subtitle_model.utterances:
                text = "".join(cue.source.text for cue in utt.cues).rstrip("，。,.")
                segments.append(AsrSegment(
                    id=utt.utt_id,
                    start_ms=utt.start_ms,
                    end_ms=utt.end_ms,
                    text=text,
                    speaker=utt.speaker.id,
                    emotion=resolve_emotion(utt.speaker.emotion.label) if utt.speaker.emotion else "neutral",
                    gender=utt.speaker.gender,
                ))

            now = datetime.now(timezone.utc).isoformat()
            asr_model = AsrModel(
                media=AsrMediaInfo(duration_ms=audio_duration_ms or 0),
                segments=segments,
                history=AsrHistory(rev=1, created_at=now, updated_at=now),
            )
            asr_model.update_fingerprint()

            # ── LLM 初始化（reseg + emotion_correct 共用）──
            reseg_config = ctx.config.get("phases", {}).get("reseg", {})
            reseg_enabled = reseg_config.get("enabled", ctx.config.get("reseg_enabled", True))
            emotion_correct_enabled = phase_config.get("emotion_correct_enabled", True)

            translate_fn = None
            if (reseg_enabled or emotion_correct_enabled) and segments:
                translate_fn = self._create_llm_fn(ctx, reseg_config)

            # ── Reseg（断句优化，合并自原 ResegPhase）──
            reseg_metrics = {}
            if reseg_enabled and segments and translate_fn:
                reseg_metrics = self._run_reseg(
                    ctx, asr_model, raw_response, reseg_config,
                    translate_fn,
                )

            # ── Emotion Correct（LLM 情绪修正）──
            emotion_metrics = {}
            if emotion_correct_enabled and asr_model.segments and translate_fn:
                emotion_metrics = self._run_emotion_correct(
                    ctx, asr_model, translate_fn,
                )

            # ── 写入 SRC cues 到 DB（reseg + emotion 修正后的最终结果）──
            if ctx.store and ctx.episode_id:
                ctx.store.delete_episode_utterances(ctx.episode_id)
                ctx.store.delete_episode_cues(ctx.episode_id)

                # Store raw ASR speaker cluster ID. Roles are created manually by the user.
                cue_rows = []
                for seg in asr_model.segments:
                    cue_rows.append({
                        "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms,
                        "text": seg.text,
                        "speaker": seg.speaker or "0",
                        "emotion": seg.emotion or "neutral",
                        "kind": seg.type if hasattr(seg, "type") and seg.type else "speech",
                        "gender": seg.gender,
                    })
                ctx.store.insert_cues(ctx.episode_id, cue_rows)
                info(f"Wrote {len(cue_rows)} SRC cues to DB (after reseg + emotion)")

            metrics = {
                "utterances_count": len(subtitle_model.utterances),
                "cues_count": total_cues,
                "segments_count": len(asr_model.segments),
            }
            if reseg_metrics:
                metrics["reseg"] = reseg_metrics
            if emotion_metrics:
                metrics["emotion_correct"] = emotion_metrics

            return PhaseResult(
                status="succeeded",
                outputs=[],
                metrics=metrics,
            )

        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )

    def _create_llm_fn(self, ctx: RunContext, llm_config: dict):
        """创建 LLM 调用函数（reseg + emotion_correct 共用）。

        Args:
            ctx: 运行上下文
            llm_config: LLM 配置（通常取 phases.reseg 配置）

        Returns:
            translate_fn 或 None（API key 缺失或初始化失败时）
        """
        engine = llm_config.get("engine")
        if not engine:
            model_name = llm_config.get("model", ctx.config.get("mt_model", ""))
            if model_name.startswith("gemini"):
                engine = "gemini"
            elif model_name.startswith("gpt") or model_name.startswith("o1"):
                engine = "openai"
            else:
                engine = ctx.config.get("mt_engine", "gemini")

        engine = engine.lower()
        is_gemini = (engine == "gemini")

        if is_gemini:
            from dubora_core.config.settings import get_gemini_key
            model = llm_config.get(
                "model",
                ctx.config.get("mt_model", ctx.config.get("gemini_model", "gemini-2.0-flash")),
            )
            api_key = llm_config.get("api_key") or get_gemini_key()
            if not api_key:
                warning("LLM skipped: Gemini API key not found")
                return None
            temperature = llm_config.get("temperature", 0.3)
            info(f"LLM using Gemini engine: {model}")
        else:
            from dubora_core.config.settings import get_openai_key
            model = llm_config.get(
                "model",
                ctx.config.get("mt_model", ctx.config.get("openai_model", "gpt-4o-mini")),
            )
            api_key = llm_config.get("api_key") or get_openai_key()
            if not api_key:
                warning("LLM skipped: OpenAI API key not found")
                return None
            temperature = llm_config.get("temperature", 0.3)
            info(f"LLM using OpenAI engine: {model}")

        try:
            from dubora_pipeline.processors.mt.time_aware_impl import create_translate_fn
            return create_translate_fn(
                api_key=api_key,
                model=model,
                temperature=temperature,
            )
        except Exception as e:
            warning(f"LLM init failed: {e}")
            return None

    def _run_reseg(
        self,
        ctx: RunContext,
        asr_model,
        raw_response: dict,
        reseg_config: dict,
        translate_fn,
    ) -> dict:
        """执行 reseg 断句优化（合并自原 ResegPhase）。

        Returns:
            metrics 字典（空字典表示跳过或无拆分）
        """
        segments = asr_model.segments

        # 读取 reseg 参数
        min_chars = reseg_config.get(
            "min_chars", ctx.config.get("reseg_min_chars", 6),
        )
        max_chars_trigger = reseg_config.get(
            "max_chars_trigger", ctx.config.get("reseg_max_chars_trigger", 25),
        )
        max_duration_trigger = reseg_config.get(
            "max_duration_trigger", ctx.config.get("reseg_max_duration_trigger", 6000),
        )

        # 调用 processor
        from dubora_pipeline.processors.reseg import run as reseg_run
        result = reseg_run(
            segments=segments,
            asr_result=raw_response,
            min_chars=min_chars,
            max_chars_trigger=max_chars_trigger,
            max_duration_trigger=max_duration_trigger,
            translate_fn=translate_fn,
        )

        metrics = {
            "candidates_count": result.candidates_count,
            "split_count": result.split_count,
            "new_segments_count": result.new_segments_count,
        }

        if result.split_count == 0:
            info("Reseg: no segments were split")
            return metrics

        # 替换 segments（内存中，最终由 run() 统一写 DB）
        asr_model.segments = result.new_segments
        asr_model.bump_rev()
        asr_model.update_fingerprint()

        info(f"Reseg: {result.split_count} segments split, {result.new_segments_count} new segments")
        return metrics

    def _run_emotion_correct(
        self,
        ctx: RunContext,
        asr_model,
        translate_fn,
    ) -> dict:
        """执行 LLM 情绪修正。

        Returns:
            metrics 字典（空字典表示跳过或无修正）
        """
        from dubora_pipeline.processors.emotion import run as emotion_run

        result = emotion_run(
            segments=asr_model.segments,
            translate_fn=translate_fn,
        )

        if result.corrected_count == 0:
            info("Emotion correct: no corrections needed")
            return {"corrected_count": 0}

        # 应用修正到 segments
        for seg in asr_model.segments:
            if seg.id in result.corrections:
                old = seg.emotion
                seg.emotion = result.corrections[seg.id]
                info(f"  {seg.id}: {old} -> {seg.emotion}")

        asr_model.bump_rev()
        asr_model.update_fingerprint()

        info(f"Emotion correct: {result.corrected_count} segments corrected")
        return {"corrected_count": result.corrected_count}
