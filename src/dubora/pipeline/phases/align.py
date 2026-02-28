"""
Align Phase: 时间对齐与重断句（不调模型）

职责：
- 读取 dub.json（AsrModel SSOT）+ mt_output.jsonl
- 为每个 segment 写入 text_en 和 tts_policy
- 生成 en.srt
- 写回更新后的 dub.json
"""
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.mt.utterance_translate import (
    estimate_en_duration_ms,
    calculate_extend_ms,
    resegment_utterance,
)
from dubora.schema.asr_model import AsrModel
from dubora.utils.timecode import write_srt_from_segments
from dubora.utils.logger import info, warning


def probe_duration_ms(audio_path: str) -> int:
    """
    Probe audio duration using ffprobe.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in milliseconds

    Raises:
        RuntimeError: If ffprobe fails or returns invalid duration
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        raise RuntimeError(f"ffprobe returned invalid duration for {audio_path}")

    duration_sec = float(duration_str)
    return int(duration_sec * 1000)


class AlignPhase(Phase):
    """时间对齐与重断句 Phase（不调模型）。"""

    name = "align"
    version = "1.1.0"

    def requires(self) -> list[str]:
        """需要 mt.mt_output 和 demux.audio。

        注意：dub.json 直接从磁盘读取（不通过 manifest requires），
        因为 align 既读又写 dub.json，如果声明为 requires 会造成指纹循环。
        """
        return ["mt.mt_output", "demux.audio"]

    def provides(self) -> list[str]:
        """生成 subs.en_srt, dub.dub_manifest (updated dub.json)。"""
        return ["subs.en_srt", "dub.dub_manifest"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Align Phase。

        流程：
        1. 直接从磁盘读取 source/dub.json（不通过 inputs）
        2. 读取 mt_output.jsonl（翻译结果）
        3. 对每个 segment 写入 text_en + tts_policy
        4. 生成 en.srt
        5. 写回更新后的 dub.json
        """
        # 直接从磁盘读取 dub.json（不通过 inputs，避免指纹循环）
        dub_json_path = Path(ctx.workspace) / "source" / "dub.json"
        if not dub_json_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"dub.json not found: {dub_json_path}",
                ),
            )

        mt_output_artifact = inputs["mt.mt_output"]
        mt_output_path = Path(ctx.workspace) / mt_output_artifact.relpath

        if not mt_output_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"MT output file not found: {mt_output_path}",
                ),
            )

        # Probe audio duration from demux.audio (SSOT for total duration)
        audio_artifact = inputs["demux.audio"]
        audio_path = Path(ctx.workspace) / audio_artifact.relpath
        if not audio_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Audio file not found: {audio_path}",
                ),
            )

        try:
            audio_duration_ms = probe_duration_ms(str(audio_path))
            info(f"Probed audio duration: {audio_duration_ms}ms ({audio_duration_ms/1000:.2f}s)")
        except RuntimeError as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=str(e),
                ),
            )

        # 读取 dub.json (AsrModel)
        with open(dub_json_path, "r", encoding="utf-8") as f:
            asr_model = AsrModel.from_dict(json.load(f))

        # Update media duration from probe
        asr_model.media.duration_ms = audio_duration_ms

        if not asr_model.segments:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No segments found in dub.json",
                ),
            )

        # 读取 mt_output.jsonl
        mt_output_map = {}
        with open(mt_output_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                mt_output = json.loads(line)
                utt_id = mt_output.get("utt_id", "")
                mt_output_map[utt_id] = mt_output

        if not mt_output_map:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No translations found in mt_output.jsonl",
                ),
            )

        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("align", {})
        tts_config = ctx.config.get("phases", {}).get("tts", {})
        default_max_rate = float(tts_config.get("max_rate", 1.3))
        min_tts_window_ms = int(tts_config.get("min_tts_window_ms", 900))
        max_extend_cap_ms = int(tts_config.get("max_extend_cap_ms", 800))
        default_allow_extend_ms = int(tts_config.get("allow_extend_ms", 500))

        # 处理每个 segment
        all_segments_for_srt = []
        translated_count = 0

        # Build sorted list of segments for gap calculation
        sorted_segments = sorted(asr_model.segments, key=lambda s: s.start_ms)

        for idx, seg in enumerate(sorted_segments):
            # 获取翻译结果
            mt_output = mt_output_map.get(seg.id)
            if not mt_output:
                warning(f"Translation not found for segment {seg.id}, skipping")
                continue

            en_text = mt_output.get("target", {}).get("text", "")
            if not en_text:
                continue

            # 检查文本是否只包含标点符号
            text_without_punc = re.sub(r'[^\w\s]', '', en_text.strip())
            text_without_punc = re.sub(r'\s+', '', text_without_punc)
            if not text_without_punc:
                warning(f"  {seg.id}: Translation contains only punctuation: {repr(en_text)}, skipping")
                continue

            # 反作弊校验
            if re.search(r"<<NAME_\d+", en_text):
                warning(f"Align: mt_output still contains NAME placeholder for {seg.id}: {en_text[:200]}")

            # 写入翻译结果
            seg.text_en = en_text
            translated_count += 1

            # 计算 tts_policy
            budget_ms = seg.end_ms - seg.start_ms

            # 动态 allow_extend_ms：不跟下一句重叠即可
            gap_to_next_ms = None
            if idx + 1 < len(sorted_segments):
                next_start = sorted_segments[idx + 1].start_ms
                gap_to_next_ms = next_start - seg.end_ms

            if gap_to_next_ms is not None and gap_to_next_ms > 0:
                utt_allow_extend_ms = max(0, gap_to_next_ms - 60)
            else:
                gap_to_end = audio_duration_ms - seg.end_ms
                if gap_to_end > 0:
                    utt_allow_extend_ms = gap_to_end
                else:
                    utt_allow_extend_ms = default_allow_extend_ms

            # Short utterance protection
            if budget_ms < min_tts_window_ms:
                utt_allow_extend_ms = max(
                    utt_allow_extend_ms,
                    min(min_tts_window_ms - budget_ms, max_extend_cap_ms),
                )
                info(f"  {seg.id}: budget={budget_ms}ms < {min_tts_window_ms}ms, allow_extend_ms={utt_allow_extend_ms}ms")

            seg.tts_policy = {
                "max_rate": default_max_rate,
                "allow_extend_ms": utt_allow_extend_ms,
            }

            # 重断句（用于 en.srt）
            end_ms_final = seg.end_ms
            srt_segments = resegment_utterance(
                en_text=en_text,
                utt_start_ms=seg.start_ms,
                utt_end_ms=end_ms_final,
                target_wps=2.5,
            )

            for srt_seg in srt_segments:
                all_segments_for_srt.append({
                    "start": srt_seg.get("start_ms", 0) / 1000.0,
                    "end": srt_seg.get("end_ms", 0) / 1000.0,
                    "en_text": srt_seg.get("text", ""),
                })

        # 更新 dub.json
        asr_model.bump_rev()
        asr_model.update_fingerprint()

        dub_manifest_path = outputs.get("dub.dub_manifest")
        dub_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dub_manifest_path, "w", encoding="utf-8") as f:
            json.dump(asr_model.to_dict(), f, indent=2, ensure_ascii=False)
        info(f"Saved updated dub.json: {translated_count} segments translated")

        # 生成 en.srt
        all_segments_for_srt.sort(key=lambda x: x["start"])
        all_segments_for_srt = [seg for seg in all_segments_for_srt if seg.get("en_text", "").strip()]

        # 硬校验：确保没有占位符
        for seg in all_segments_for_srt:
            en_text = seg.get("en_text", "")
            remaining_placeholders = re.findall(r"<<NAME_\d+>>", en_text)
            if remaining_placeholders:
                raise AssertionError(
                    f"en.srt contains placeholder: {remaining_placeholders}. Text: {en_text[:200]}"
                )

        en_srt_path = outputs.get("subs.en_srt")
        write_srt_from_segments(all_segments_for_srt, str(en_srt_path), text_key="en_text")
        info(f"Saved en.srt: {len(all_segments_for_srt)} segments")

        return PhaseResult(
            status="succeeded",
            outputs=[
                "subs.en_srt",
                "dub.dub_manifest",
            ],
            metrics={
                "segments_count": len(asr_model.segments),
                "translated_count": translated_count,
                "srt_segments_count": len(all_segments_for_srt),
                "audio_duration_ms": audio_duration_ms,
            },
        )
