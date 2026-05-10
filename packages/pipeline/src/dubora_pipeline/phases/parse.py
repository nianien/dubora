"""Parse Phase: ASR 结果 → DB cues

支持两种主轴（由 ASR_PRIMARY 配置决定）：

- doubao 主轴：三源融合（doubao 时间轴 + tencent 分段 + fish 文本 LLM 校准）
- 其他主轴（gemini 等）：直通——直接用主模型输出做 emotion 回填 + end_ms 延长

公共后处理：emotion 回填 + end_ms 延长 + 构建 cue rows → 写文件 + 写 DB
"""
import json
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_core.config import resolve_emotion
from dubora_core.config.settings import get_gemini_key
from dubora_core.utils.logger import info


class ParsePhase(Phase):
    """ASR 结果 → cue rows。"""

    name = "parse"
    version = "4.1.0"

    def requires(self) -> list[str]:
        return []  # 实际由 _LazyPhase 动态指定 [f"asr.{primary}"]

    def provides(self) -> list[str]:
        return []

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        primary = ctx.config.get("asr_primary", "doubao")
        info(f"Parse: primary={primary}")

        if primary != "doubao":
            return self._run_direct(primary, ctx, inputs)

        return self._run_doubao_fusion(ctx, inputs)

    def _run_doubao_fusion(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
    ) -> PhaseResult:
        doubao_path = Path(ctx.workspace) / inputs["asr.doubao"].relpath
        tencent_path = Path(ctx.workspace) / "asr-tencent.json"
        fish_path = Path(ctx.workspace) / "asr-fish.json"

        from dubora_pipeline.processors.asr.fusion import (
            get_doubao_utterances, split_long_utterances, get_tencent_segments,
            call_llm_diff, build_align_input, call_llm_align, apply_alignment,
            clamp_overlaps, fill_null_emotions, extend_end_ms,
        )

        try:
            # 1. 读取数据（tencent/fish 可选）
            with open(doubao_path, "r", encoding="utf-8") as f:
                doubao_raw = json.load(f)
            tencent_raw = None
            if tencent_path.exists():
                with open(tencent_path, "r", encoding="utf-8") as f:
                    tencent_raw = json.load(f)
            fish_data = {}
            if fish_path.exists():
                with open(fish_path, "r", encoding="utf-8") as f:
                    fish_data = json.load(f)

            total_ms = doubao_raw["audio_info"]["duration"]
            doubao_utts = split_long_utterances(get_doubao_utterances(doubao_raw))
            tencent_segs = get_tencent_segments(tencent_raw) if tencent_raw else []

            # 落盘拆分结果供 check
            asr_dir = doubao_path.parent
            split_path = asr_dir / "asr-doubao-split.json"
            with open(split_path, "w", encoding="utf-8") as f:
                json.dump(doubao_utts, f, indent=2, ensure_ascii=False)

            info(f"Parse: doubao={len(doubao_utts)}, tencent={len(tencent_segs)}, total={total_ms}ms")

            if not doubao_utts:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(type="ValueError", message="Doubao utterances is empty"),
                )

            # 2. LLM diff: 主本(doubao) vs 副本(fish)，已有缓存则跳过
            gemini_key = get_gemini_key()
            gemini_model = ctx.config.get("asr_gemini_model", "gemini-3.1-pro-preview")

            diff_path = asr_dir / "asr-llm-diff.json"
            if diff_path.exists() and diff_path.stat().st_size > 0:
                info("Parse: loading cached LLM diff")
                with open(diff_path, "r", encoding="utf-8") as f:
                    llm_result = json.load(f)
            elif fish_data.get("text", ""):
                primary_text = "".join(u["text"] for u in doubao_utts)
                secondary_text = fish_data["text"]
                info(f"LLM diff input primary ({len(primary_text)} chars): {primary_text}")
                info(f"LLM diff input secondary ({len(secondary_text)} chars): {secondary_text}")

                if not gemini_key:
                    return PhaseResult(
                        status="failed",
                        error=ErrorInfo(type="ConfigError", message="GEMINI_API_KEY not set"),
                    )

                llm_result = call_llm_diff(
                    primary_text, secondary_text,
                    model_name=gemini_model, api_key=gemini_key,
                )
            else:
                info("Parse: fish text empty, skip LLM diff")
                llm_result = {}

            llm_diff = llm_result.get("diff", [])

            # 3. 三源融合（LLM 对齐 Fish 文本到腾讯时间窗口）
            align_input_path = asr_dir / "asr-llm-align-input.json"
            align_path = asr_dir / "asr-llm-align.json"
            align_input = build_align_input(doubao_utts, tencent_segs, llm_diff, total_ms, fish_data)

            if align_input is None:
                align_result = {}
            elif align_path.exists() and align_path.stat().st_size > 0:
                info("Parse: loading cached LLM align")
                with open(align_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                align_result = {"aligned": raw, "unmatched_windows": []} if isinstance(raw, list) else raw
            elif not gemini_key:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(type="ConfigError", message="GEMINI_API_KEY not set"),
                )
            else:
                with open(align_input_path, "w", encoding="utf-8") as f:
                    json.dump(align_input, f, indent=2, ensure_ascii=False)
                align_result = call_llm_align(align_input, model_name=gemini_model, api_key=gemini_key)
                with open(align_path, "w", encoding="utf-8") as f:
                    json.dump(align_result, f, indent=2, ensure_ascii=False)

            aligned = align_result.get("aligned", [])
            filled = apply_alignment(aligned, tencent_segs, doubao_utts, total_ms) if aligned else []
            merged = sorted(doubao_utts + filled, key=lambda x: x["start_ms"])
            merged = clamp_overlaps(merged)

            # 4. emotion 回填 + end_ms 延长
            fill_null_emotions(merged)
            merged = extend_end_ms(merged)

            # 保存 LLM diff 中间结果（排查用，缓存命中时不覆盖）
            if not diff_path.exists():
                with open(diff_path, "w", encoding="utf-8") as f:
                    json.dump(llm_result, f, indent=2, ensure_ascii=False)

            return _finalize_cues(merged, ctx, asr_dir, extra_metrics={
                "doubao_count": len(doubao_utts),
                "filled_count": len(filled),
                "diff_count": len(llm_diff),
            })

        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )

    def _run_direct(
        self,
        primary: str,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
    ) -> PhaseResult:
        """非 doubao 主轴：直接用主模型输出生成 cues。"""
        from dubora_pipeline.processors.asr.fusion import fill_null_emotions, extend_end_ms

        artifact_key = f"asr.{primary}"
        primary_path = Path(ctx.workspace) / inputs[artifact_key].relpath

        try:
            with open(primary_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            segments = _extract_segments(primary, raw)
            if not segments:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(type="ValueError", message=f"{primary} segments empty"),
                )

            info(f"Parse: loaded {len(segments)} segments from {primary_path.name}")

            fill_null_emotions(segments)
            segments = extend_end_ms(segments)

            return _finalize_cues(segments, ctx, primary_path.parent, extra_metrics={
                f"{primary}_count": len(segments),
            })

        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )


def _extract_segments(primary: str, raw) -> list[dict]:
    """从 asr-{primary}.json 取出统一格式的 segments。

    输出字段：start_ms / end_ms / text / speaker / emotion / gender
    """
    if primary == "gemini":
        return raw.get("utterances", []) if isinstance(raw, dict) else raw

    raise ValueError(
        f"Unsupported asr_primary={primary} for direct path. "
        f"Add a normalizer in _extract_segments to map its output to the common cue shape."
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
