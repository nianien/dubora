"""
Voiceprint Phase: 声纹识别（Sub 之后、MT 之前）

输入:
  - extract.vocals: 分离后的人声音频
  - asr.asr_result: ASR 原始结果（含 speaker 分段）

输出:
  - voiceprint.speaker_map: speaker -> char_id 映射（JSON）
  - voiceprint.reference_clips: 参考音频片段目录

声纹库存储在剧级目录：{series_dub}/voiceprint/library.json
"""
import json
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import (
    Artifact,
    ErrorInfo,
    PhaseResult,
    RunContext,
    ResolvedOutputs,
)
from dubora.pipeline.processors.voiceprint import run_voiceprint
from dubora.utils.logger import info


class VoiceprintPhase(Phase):
    """声纹识别 Phase。"""

    name = "voiceprint"
    version = "1.0.0"

    def requires(self) -> list[str]:
        """需要 extract.vocals（人声音频）和 asr.asr_result（ASR 结果）。"""
        return ["extract.vocals", "asr.asr_result"]

    def provides(self) -> list[str]:
        """生成 speaker_map 和参考音频片段。"""
        return ["voiceprint.speaker_map", "voiceprint.reference_clips"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Voiceprint Phase。

        流程：
        1. 读取 vocals.wav 和 ASR 结果
        2. 解析 segments（speaker, start_ms, end_ms, gender）
        3. 调用 voiceprint processor
        4. 保存 speaker_map.json 和参考音频
        """
        # 获取输入
        vocals_artifact = inputs.get("extract.vocals")
        if not vocals_artifact:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="extract.vocals artifact not found. Make sure sep phase completed.",
                ),
            )

        asr_artifact = inputs.get("asr.asr_result")
        if not asr_artifact:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="asr.asr_result artifact not found. Make sure asr phase completed.",
                ),
            )

        workspace_path = Path(ctx.workspace)
        vocals_path = workspace_path / vocals_artifact.relpath
        asr_path = workspace_path / asr_artifact.relpath

        if not vocals_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Vocals file not found: {vocals_path}",
                ),
            )

        if not asr_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"ASR result file not found: {asr_path}",
                ),
            )

        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("voiceprint", {})
        match_threshold = phase_config.get("match_threshold", 0.65)
        ema_alpha = phase_config.get("ema_alpha", 0.3)
        ref_duration_s = phase_config.get("ref_duration_s", 8.0)

        # 声纹库路径：剧级目录（workspace.parent = series dub dir）
        series_dub_dir = workspace_path.parent
        library_path = str(series_dub_dir / "voiceprint" / "library.json")

        try:
            # 读取 ASR 结果，解析 segments
            with open(asr_path, "r", encoding="utf-8") as f:
                asr_data = json.load(f)

            result_data = asr_data.get("result") or {}
            raw_utterances = result_data.get("utterances") or []

            # 转换为 processor 所需的 segment 格式
            segments = []
            for utt in raw_utterances:
                segments.append({
                    "speaker": str(utt.get("speaker", "0")),
                    "start_ms": int(utt.get("start_time", 0)),
                    "end_ms": int(utt.get("end_time", 0)),
                    "gender": (utt.get("additions") or {}).get("gender"),
                })

            if not segments:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="ValueError",
                        message="No utterances found in ASR result",
                    ),
                )

            info(f"Voiceprint: {len(segments)} segments from ASR result")

            # 输出目录
            output_dir = str(outputs.get("voiceprint.speaker_map").parent)

            # 调用 processor
            result = run_voiceprint(
                vocals_path=str(vocals_path),
                segments=segments,
                library_path=library_path,
                output_dir=output_dir,
                match_threshold=match_threshold,
                ema_alpha=ema_alpha,
                ref_duration_s=ref_duration_s,
            )

            if result.error:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="ProcessorError",
                        message=str(result.error),
                    ),
                )

            speaker_map = result.data["speaker_map"]

            # 保存 speaker_map.json
            speaker_map_path = outputs.get("voiceprint.speaker_map")
            speaker_map_path.parent.mkdir(parents=True, exist_ok=True)
            with open(speaker_map_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "speaker_map": speaker_map,
                        "total_speakers": len(speaker_map),
                        "library_path": library_path,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )

            info(f"Saved speaker_map: {len(speaker_map)} mappings -> {speaker_map_path}")

            return PhaseResult(
                status="succeeded",
                outputs=["voiceprint.speaker_map", "voiceprint.reference_clips"],
                metrics=result.metrics,
            )

        except Exception as e:
            import traceback

            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                    traceback=traceback.format_exc(),
                ),
            )
