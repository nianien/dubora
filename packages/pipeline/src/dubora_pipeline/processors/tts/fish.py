"""
TTS Synthesis: Fish Audio per-segment with episode-level caching.

Uses Fish Audio SDK for voice cloning via reference audio.
Reuses audio alignment logic from azure.py (same as volcengine.py).
"""
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from dubora_core.infra.fish_tts_client import call_fish_tts as _call_fish_tts
from dubora_pipeline.schema.dub_manifest import DubManifest
from dubora_pipeline.schema.tts_report import TTSReport, TTSSegmentReport, TTSSegmentStatus

from .azure import (
    _trim_silence,
    _normalize_audio_format,
    _create_silent_audio,
)

# Cache configuration
CACHE_ENGINE = "fish"
CACHE_ENGINE_VER = "v1"
CACHE_SAMPLE_RATE = 24000
CACHE_CHANNELS = 1


def _normalize_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _generate_cache_key(text: str, reference_id: str) -> str:
    key_parts = [
        CACHE_ENGINE,
        CACHE_ENGINE_VER,
        _normalize_text(text),
        reference_id,
    ]
    key_str = "|".join(key_parts)
    return hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]


def _write_cache_atomic(cache_file: Path, source_file: Path):
    temp_file = cache_file.with_suffix(".tmp")
    shutil.copy2(source_file, temp_file)
    temp_file.replace(cache_file)


def _get_duration_ms(audio_path: str) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
    )
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        return 0
    return int(float(duration_str) * 1000)


def _pad_audio(input_path: str, output_path: str, target_ms: int):
    current_ms = _get_duration_ms(input_path)
    if current_ms >= target_ms:
        shutil.copy2(input_path, output_path)
        return
    pad_duration_sec = (target_ms - current_ms) / 1000.0
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-af", f"apad=pad_dur={pad_duration_sec}",
        "-ar", str(CACHE_SAMPLE_RATE),
        "-ac", str(CACHE_CHANNELS),
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _apply_rate_and_pad(input_path: str, output_path: str, rate: float, target_ms: int):
    if rate > 2.0:
        ratios = []
        remaining = rate
        while remaining > 2.0:
            ratios.append(2.0)
            remaining /= 2.0
        ratios.append(remaining)
        filter_str = ",".join([f"atempo={r}" for r in ratios])
    elif rate < 0.5:
        ratios = []
        remaining = rate
        while remaining < 0.5:
            ratios.append(0.5)
            remaining /= 0.5
        ratios.append(remaining)
        filter_str = ",".join([f"atempo={r}" for r in ratios])
    else:
        filter_str = f"atempo={rate}"

    target_sec = target_ms / 1000.0
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-af", f"{filter_str},apad=whole_dur={target_sec}",
        "-t", str(target_sec),
        "-ar", str(CACHE_SAMPLE_RATE),
        "-ac", str(CACHE_CHANNELS),
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def synthesize_tts_per_segment(
    dub_manifest: DubManifest,
    voice_assignment: Dict[str, Any],
    segments_dir: str,
    temp_dir: str,
    *,
    api_key: str,
    max_workers: int = 4,
) -> TTSReport:
    """
    Per-segment TTS synthesis using Fish Audio (voice cloning).

    Each utterance is synthesized using the speaker's sample_audio as reference.

    Args:
        dub_manifest: DubManifest object
        voice_assignment: Speaker -> {voice_type, sample_audio, tts_provider, ...} mapping
        segments_dir: Output directory for per-segment WAVs
        temp_dir: Temporary directory for intermediate files
        api_key: Fish Audio API key
        max_workers: Number of concurrent workers (reserved for future use)

    Returns:
        TTSReport with per-segment synthesis results
    """
    output_dir = Path(segments_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    # Cache directory
    cache_dir = temp_path / CACHE_ENGINE
    cache_dir.mkdir(parents=True, exist_ok=True)

    segment_reports: List[TTSSegmentReport] = []

    for utt in dub_manifest.utterances:
        utt_id = utt.utt_id
        text = utt.text_en.strip()
        budget_ms = utt.budget_ms
        speaker = utt.speaker
        max_rate = utt.tts_policy.max_rate
        allow_extend_ms = utt.tts_policy.allow_extend_ms

        segment_file = output_dir / f"seg_{utt_id}.wav"
        segment_file_raw = temp_path / f"seg_{utt_id}_raw.wav"

        if not text:
            _create_silent_audio(str(segment_file), budget_ms / 1000.0)
            segment_reports.append(
                TTSSegmentReport(
                    utt_id=utt_id,
                    budget_ms=budget_ms,
                    raw_ms=0,
                    trimmed_ms=0,
                    final_ms=budget_ms,
                    rate=1.0,
                    status=TTSSegmentStatus.SUCCESS,
                    output_path=str(segment_file.relative_to(output_dir.parent)),
                )
            )
            continue

        voice_info = voice_assignment["speakers"].get(speaker, {})
        sample_audio = voice_info.get("sample_audio_local", "")
        reference_id = voice_info.get("voice_type", "")

        if not sample_audio and not reference_id:
            segment_reports.append(
                TTSSegmentReport(
                    utt_id=utt_id,
                    budget_ms=budget_ms,
                    raw_ms=0,
                    trimmed_ms=0,
                    final_ms=0,
                    rate=1.0,
                    status=TTSSegmentStatus.FAILED,
                    output_path="",
                    error=f"Fish TTS: no sample_audio or reference_id for speaker {speaker}",
                )
            )
            continue

        # Cache key uses sample_audio path or reference_id as voice identifier
        voice_key = reference_id or sample_audio
        cache_key = _generate_cache_key(text, voice_key)
        cache_file = cache_dir / f"{cache_key}.wav"

        try:
            if cache_file.exists():
                shutil.copy2(cache_file, segment_file_raw)
                print(f"  [fish] [{utt_id}] Cache hit")
            else:
                audio_bytes = _call_fish_tts(
                    text=text,
                    api_key=api_key,
                    reference_id=reference_id if reference_id else None,
                    reference_audio=sample_audio if sample_audio and not reference_id else None,
                )

                # Fish SDK returns audio stream (usually MP3/WAV)
                # Write to temp file and normalize to standard WAV
                temp_audio = temp_path / f"seg_{utt_id}_temp.audio"
                with open(temp_audio, "wb") as f:
                    f.write(audio_bytes)

                _normalize_audio_format(
                    str(temp_audio),
                    str(segment_file_raw),
                    sample_rate=CACHE_SAMPLE_RATE,
                    channels=CACHE_CHANNELS,
                )
                temp_audio.unlink(missing_ok=True)

                _write_cache_atomic(cache_file, segment_file_raw)

            # Get raw duration
            raw_ms = _get_duration_ms(str(segment_file_raw))

            # Trim silence (only when raw > budget)
            trimmed_file = temp_path / f"seg_{utt_id}_trimmed.wav"
            if raw_ms <= budget_ms:
                shutil.copy2(str(segment_file_raw), str(trimmed_file))
                trimmed_ms = raw_ms
            else:
                trimmed_sec, saved_ms = _trim_silence(str(segment_file_raw), str(trimmed_file))
                trimmed_ms = int(trimmed_sec * 1000)

            # Determine rate and status
            if trimmed_ms <= budget_ms:
                _pad_audio(str(trimmed_file), str(segment_file), budget_ms)
                final_ms = budget_ms
                rate = 1.0
                status = TTSSegmentStatus.SUCCESS
            else:
                rate = trimmed_ms / budget_ms
                if rate <= max_rate:
                    _apply_rate_and_pad(str(trimmed_file), str(segment_file), rate, budget_ms)
                    final_ms = budget_ms
                    status = TTSSegmentStatus.RATE_ADJUSTED
                elif allow_extend_ms > 0:
                    extended_budget = budget_ms + allow_extend_ms
                    if trimmed_ms <= extended_budget:
                        _pad_audio(str(trimmed_file), str(segment_file), extended_budget)
                        final_ms = extended_budget
                        rate = 1.0
                        status = TTSSegmentStatus.EXTENDED
                    elif trimmed_ms / extended_budget <= max_rate:
                        rate = trimmed_ms / extended_budget
                        _apply_rate_and_pad(str(trimmed_file), str(segment_file), rate, extended_budget)
                        final_ms = extended_budget
                        status = TTSSegmentStatus.EXTENDED
                    else:
                        raise RuntimeError(
                            f"Cannot fit: {trimmed_ms}ms > {extended_budget}ms even at {max_rate}x rate"
                        )
                else:
                    raise RuntimeError(
                        f"Cannot fit: {trimmed_ms}ms > {budget_ms}ms, would need {rate:.2f}x rate (max: {max_rate}x)"
                    )

            trimmed_file.unlink(missing_ok=True)
            segment_file_raw.unlink(missing_ok=True)

            segment_reports.append(
                TTSSegmentReport(
                    utt_id=utt_id,
                    budget_ms=budget_ms,
                    raw_ms=raw_ms,
                    trimmed_ms=trimmed_ms,
                    final_ms=final_ms,
                    rate=rate,
                    status=status,
                    output_path=str(segment_file.relative_to(output_dir.parent)),
                )
            )
            print(f"  [fish] [{utt_id}] {raw_ms}ms -> {trimmed_ms}ms -> {final_ms}ms (rate={rate:.2f}x)")

        except Exception as e:
            segment_reports.append(
                TTSSegmentReport(
                    utt_id=utt_id,
                    budget_ms=budget_ms,
                    raw_ms=0,
                    trimmed_ms=0,
                    final_ms=0,
                    rate=1.0,
                    status=TTSSegmentStatus.FAILED,
                    output_path="",
                    error=str(e),
                )
            )
            print(f"  [fish] [{utt_id}] Failed: {e}")

    return TTSReport(
        audio_duration_ms=dub_manifest.audio_duration_ms,
        segments_dir=segments_dir,
        segments=segment_reports,
    )
