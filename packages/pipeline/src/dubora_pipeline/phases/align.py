"""
Align Phase: 字幕拆分 + drift_score 安全阀

职责：
- 读 enriched utterances（从 DB，含 tts_duration_ms）
- drift_score 检查：(tts_duration_ms / physical时长) > 1.1 → 报警
- 从 DST cues 生成 en.srt
"""
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_pipeline.utils.timecode import write_srt_from_segments
from dubora_core.utils.logger import info, warning


class AlignPhase(Phase):
    """字幕拆分 + drift_score 安全阀 Phase。"""

    name = "align"
    version = "3.0.0"

    def requires(self) -> list[str]:
        """数据从 DB 读取。"""
        return []

    def provides(self) -> list[str]:
        """生成 subs.en_srt。"""
        return ["subs.en_srt"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Align Phase。

        流程：
        1. 读 enriched utterances（有 text_en 的）
        2. drift_score 检查
        3. 从 DST cues 生成 en.srt
        """
        store = ctx.store
        episode_id = ctx.episode_id
        workspace_path = Path(ctx.workspace)

        if not store or not episode_id:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="Align phase requires DB store and episode_id.",
                ),
            )

        # Load utterances
        all_utts = store.get_utterances(episode_id)
        if not all_utts:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No utterances found. Run translate phase first.",
                ),
            )

        # Filter to utterances with translations
        translated_utts = [
            u for u in all_utts if u.get("text_en", "").strip()
        ]
        if not translated_utts:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No translated utterances found.",
                ),
            )

        info(f"Align: {len(translated_utts)} translated utterances out of {len(all_utts)} total")

        # Load role names for drift warning display
        ep = store.get_episode(episode_id)
        drama_id = ep["drama_id"] if ep else 0
        role_names = store.get_role_name_map(drama_id)

        # Drift score check using tts_duration_ms from DB
        drift_warnings = []
        for utt in translated_utts:
            physical_ms = utt["end_ms"] - utt["start_ms"]
            if physical_ms <= 0:
                continue

            tts_ms = utt.get("tts_duration_ms") or 0
            if tts_ms > 0:
                drift = tts_ms / physical_ms
                if drift > 1.1:
                    speaker_id = utt.get("speaker", "")
                    try:
                        speaker_display = role_names.get(int(speaker_id), str(speaker_id))
                    except (ValueError, TypeError):
                        speaker_display = str(speaker_id)
                    drift_warnings.append(
                        f"utterance {utt['id']} ({speaker_display}): "
                        f"drift={drift:.2f} (TTS={tts_ms}ms / physical={physical_ms}ms)"
                    )

        if drift_warnings:
            for w in drift_warnings:
                warning(f"Drift score warning: {w}")
            info(f"Align: {len(drift_warnings)} utterances have drift_score > 1.1")

        # Build SRT from cues' text_en
        all_cues = store.get_cues(episode_id)
        all_srt_segments = []

        for cue in all_cues:
            text_en = (cue.get("text_en") or "").strip()
            if not text_en:
                continue
            all_srt_segments.append({
                "start": cue["start_ms"] / 1000.0,
                "end": cue["end_ms"] / 1000.0,
                "en_text": text_en,
            })

        all_srt_segments.sort(key=lambda x: x["start"])

        en_srt_path = outputs.get("subs.en_srt")
        en_srt_path.parent.mkdir(parents=True, exist_ok=True)
        write_srt_from_segments(all_srt_segments, str(en_srt_path), text_key="en_text")
        info(f"Saved en.srt: {len(all_srt_segments)} segments from DST cues")

        return PhaseResult(
            status="succeeded",
            outputs=["subs.en_srt"],
            metrics={
                "utterances_count": len(translated_utts),
                "srt_segments_count": len(all_srt_segments),
                "drift_warnings": len(drift_warnings),
            },
        )
