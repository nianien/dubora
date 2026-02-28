"""
Sub Phase: 字幕后处理（从 ASR raw-response 生成 dub.json）

职责：
- 读取 ASR raw response（asr.asr_result，SSOT）
- 解析为 Utterance[]（使用 models/doubao/parser.py）
- 应用后处理策略（切句、speaker 处理等）
- 生成 dub.json（AsrModel 格式，pipeline 唯一 SSOT）

不负责：
- ASR 识别（由 ASR Phase 负责）
- 翻译（由 MT Phase 负责）

架构原则：
- 直接从 raw-response 生成（SSOT，包含完整语义信息）
- raw-response 是事实源，dub.json 从事实源生成
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.srt import run as srt_run
from dubora.schema.asr_model import AsrModel, AsrSegment, AsrMediaInfo, AsrHistory, AsrFingerprint
from dubora.utils.logger import info


class SubtitlePhase(Phase):
    """字幕后处理 Phase。"""

    name = "sub"
    version = "1.1.0"

    def requires(self) -> list[str]:
        """需要 asr.asr_result（word 级时间轴）。"""
        return ["asr.asr_result"]

    def provides(self) -> list[str]:
        """生成 dub.dub_manifest (SSOT dub.json)。"""
        return ["dub.dub_manifest"]

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
        3. 应用后处理策略生成 SubtitleModel
        4. 转换为 AsrModel 格式写入 dub.json
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
            from dubora.models.doubao.parser import parse_utterances

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

        phase_config = ctx.config.get("phases", {}).get("sub", {})
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

            # 将 SubtitleModel 转换为 AsrModel 格式（dub.json SSOT）
            segments = []
            for utt in subtitle_model.utterances:
                segments.append(AsrSegment(
                    id=utt.utt_id,
                    start_ms=utt.start_ms,
                    end_ms=utt.end_ms,
                    text="".join(cue.source.text for cue in utt.cues),
                    speaker=utt.speaker.id,
                    emotion=utt.speaker.emotion.label if utt.speaker.emotion else "neutral",
                    gender=utt.speaker.gender,
                    speech_rate=utt.speaker.speech_rate.zh_tps,
                ))

            now = datetime.now(timezone.utc).isoformat()
            asr_model = AsrModel(
                media=AsrMediaInfo(duration_ms=audio_duration_ms or 0),
                segments=segments,
                history=AsrHistory(rev=1, created_at=now, updated_at=now),
            )
            asr_model.update_fingerprint()

            # 写入 dub.json
            model_path = outputs.get("dub.dub_manifest")
            model_path.parent.mkdir(parents=True, exist_ok=True)
            with open(model_path, "w", encoding="utf-8") as f:
                json.dump(asr_model.to_dict(), f, indent=2, ensure_ascii=False)
            info(f"Saved dub.json (SSOT) to: {model_path}")

            return PhaseResult(
                status="succeeded",
                outputs=[
                    "dub.dub_manifest",
                ],
                metrics={
                    "utterances_count": len(subtitle_model.utterances),
                    "cues_count": total_cues,
                    "segments_count": len(segments),
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
