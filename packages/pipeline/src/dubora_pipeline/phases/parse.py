"""Parse Phase: 豆包 ASR 输出 → DB cues。

单源直通：读 asr-doubao.json → 提取 utterances → emotion 回填 + end_ms 延长 → 写 cues。

之前的 doubao+tencent+fish 三源融合方案已于 2026-06 下线，
因为 Gemini scene context 让单源豆包准确率达到 92%，融合不再必要。
"""
import json
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_pipeline.processors.asr.postprocess import (
    get_doubao_utterances, fill_null_emotions, extend_end_ms,
)
from dubora_core.config import resolve_emotion
from dubora_core.utils.logger import info


class ParsePhase(Phase):
    """ASR doubao 结果 → cue rows。"""

    name = "parse"
    version = "5.0.0"

    def requires(self) -> list[str]:
        return ["asr.doubao"]

    def provides(self) -> list[str]:
        return []

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        doubao_path = Path(ctx.workspace) / inputs["asr.doubao"].relpath

        try:
            with open(doubao_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            segments = get_doubao_utterances(raw)
            if not segments:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(type="ValueError", message="Doubao utterances is empty"),
                )

            info(f"Parse: loaded {len(segments)} segments from {doubao_path.name}")

            fill_null_emotions(segments)
            segments = extend_end_ms(segments)

            return _finalize_cues(segments, ctx, doubao_path.parent, extra_metrics={
                "doubao_count": len(segments),
            })

        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(type=type(e).__name__, message=str(e)),
            )


_TRAILING_PUNC = "，。,.、；：;:"


def _finalize_cues(
    segments: list[dict],
    ctx: RunContext,
    asr_dir: Path,
    extra_metrics: dict | None = None,
) -> PhaseResult:
    """构建 cue rows + 写文件 + 写 DB。"""
    cue_rows = []
    for u in segments:
        text = str(u.get("text", "")).strip().rstrip(_TRAILING_PUNC)
        if not text:
            continue
        cue_rows.append({
            "start_ms": int(u.get("start_ms", 0)),
            "end_ms": int(u.get("end_ms", 0)),
            "text": text,
            "speaker": str(u.get("speaker", "0")),
            "emotion": resolve_emotion(u.get("emotion") or "neutral"),
            "kind": u.get("type", "speech"),
            "gender": u.get("gender"),
        })

    result_path = asr_dir / "asr-result.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(cue_rows, f, indent=2, ensure_ascii=False)
    info(f"Saved {len(cue_rows)} cues to {result_path.name}")

    if ctx.store and ctx.episode_id:
        ctx.store.delete_episode_utterances(ctx.episode_id)
        ctx.store.delete_episode_cues(ctx.episode_id)
        ctx.store.insert_cues(ctx.episode_id, cue_rows)
        info(f"Wrote {len(cue_rows)} SRC cues to DB")

    metrics = {"segments_count": len(cue_rows)}
    if extra_metrics:
        metrics.update(extra_metrics)

    return PhaseResult(
        status="succeeded",
        outputs=[],
        metrics=metrics,
    )
