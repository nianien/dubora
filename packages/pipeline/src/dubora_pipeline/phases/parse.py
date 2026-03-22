"""Parse Phase: ASR 融合 → DB cues

输入：
- asr.doubao（必需）
- asr.tencent / asr.fish（可选，空洞 > 10s 时 asr phase 才会产出）

处理：
1. split doubao utterances（标点 + 时间间隔拆分）
2. 有 fish 时：LLM diff → fuse 三源融合；无 fish 时：只用 doubao
3. 歌曲标注 + emotion 回填 + end_ms 延长
4. 构建 cue rows → 写文件 + 写 DB
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
    """三源融合 Phase。"""

    name = "parse"
    version = "4.0.0"

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
        tencent_path = Path(ctx.workspace) / "asr-tencent.json"
        fish_path = Path(ctx.workspace) / "asr-fish.json"

        from dubora_pipeline.processors.asr.fusion import (
            get_doubao_utterances, split_long_utterances, get_tencent_segments,
            call_llm_diff, fuse, get_sing_ranges, clamp_overlaps,
            fill_null_emotions, extend_end_ms,
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
                gemini_key = get_gemini_key()
                gemini_model = ctx.config.get("asr_gemini_model", "gemini-3.1-pro-preview")

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

            # 3. 三源融合
            filled = fuse(doubao_utts, tencent_segs, fish_data, llm_diff, total_ms)
            merged = sorted(doubao_utts + filled, key=lambda x: x["start_ms"])
            merged = clamp_overlaps(merged)

            # 4. 歌曲标注（用时间范围匹配，不用子串）
            sing_ranges = get_sing_ranges(
                doubao_utts, fish_data,
                llm_result.get("primary_sing", []),
                llm_result.get("secondary_sing", []),
            )
            for seg in merged:
                mid = (seg["start_ms"] + seg["end_ms"]) // 2
                if any(s <= mid <= e for s, e in sing_ranges):
                    seg["type"] = "sing"

            # 5. emotion 回填 + end_ms 延长
            fill_null_emotions(merged)
            merged = extend_end_ms(merged)

            # 6. 构建 cue rows
            _TRAILING_PUNC = "，。,.、；：;:"
            cue_rows = []
            for u in merged:
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

            # 保存 LLM diff 中间结果（排查用，缓存命中时不覆盖）
            if not diff_path.exists():
                with open(diff_path, "w", encoding="utf-8") as f:
                    json.dump(llm_result, f, indent=2, ensure_ascii=False)

            # 写入本地文件
            result_path = asr_dir / "asr-result.json"
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(cue_rows, f, indent=2, ensure_ascii=False)
            info(f"Saved {len(cue_rows)} cues to {result_path.name}")

            # 写入 SRC cues 到 DB
            if ctx.store and ctx.episode_id:
                ctx.store.delete_episode_utterances(ctx.episode_id)
                ctx.store.delete_episode_cues(ctx.episode_id)
                ctx.store.insert_cues(ctx.episode_id, cue_rows)
                info(f"Wrote {len(cue_rows)} SRC cues to DB")

            return PhaseResult(
                status="succeeded",
                outputs=[],
                metrics={
                    "segments_count": len(cue_rows),
                    "doubao_count": len(doubao_utts),
                    "filled_count": len(filled),
                    "diff_count": len(llm_diff),
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
