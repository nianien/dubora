"""
TTS Synthesis: VolcEngine TTS per segment with episode-level caching.

Functions:
- synthesize_tts: Original function (concatenates to tts_en.wav)
- synthesize_tts_per_segment: New function (per-segment WAVs, no concatenation)

Reuses audio alignment logic from azure.py.
"""
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from dubora_core.config import emotion_supports_lang
from dubora_core.infra.tts_client import (
    call_volcengine_tts as _call_volcengine_tts,
    DEFAULT_FORMAT,
    DEFAULT_RESOURCE_ID,
    DEFAULT_SAMPLE_RATE,
)
from dubora_pipeline.schema.dub_manifest import DubManifest
from dubora_pipeline.schema.tts_report import TTSReport, TTSSegmentReport, TTSSegmentStatus

# For audio diagnostics
try:
    import numpy as np
    import soundfile as sf
    AUDIO_DIAGNOSTICS_AVAILABLE = True
except ImportError:
    AUDIO_DIAGNOSTICS_AVAILABLE = False

# Import audio alignment functions from azure.py
from .azure import (
    _align_segment_to_window,
    _trim_silence,
    _concatenate_with_gaps,
    _create_silent_audio,
    _normalize_audio_format,
)

# Cache configuration
CACHE_ENGINE = "volcengine"
CACHE_ENGINE_VER = "v1"
CACHE_SAMPLE_RATE = 24000
CACHE_CHANNELS = 1
CACHE_FORMAT = "wav"


def _normalize_text(text: str) -> str:
    """
    Normalize text for cache key generation.
    - strip()
    - collapse consecutive whitespace to single space
    """
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _generate_cache_key(
    text: str,
    voice_id: str,
    prosody: Dict[str, Any],
    language: str,
) -> str:
    """
    Generate cache key for a TTS segment.
    
    Args:
        text: Normalized text
        voice_id: Voice ID (speaker)
        prosody: Prosody parameters (rate, pitch, etc.)
        language: Language code
    
    Returns:
        Cache key (hex digest)
    """
    key_parts = [
        CACHE_ENGINE,
        CACHE_ENGINE_VER,
        _normalize_text(text),
        voice_id,
        json.dumps(prosody, sort_keys=True),
        language,
    ]
    key_str = "|".join(key_parts)
    return hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]


def _get_cache_paths(output_dir: Path) -> tuple[Path, Path]:
    """Get cache directory and manifest path."""
    cache_dir = output_dir / CACHE_ENGINE
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.jsonl"
    return cache_dir, manifest_path


def _append_manifest(manifest_path: Path, seg_id: int, cache_key: str, voice_id: str, text: str):
    """Append entry to cache manifest."""
    entry = {
        "seg_id": seg_id,
        "cache_key": cache_key,
        "voice_id": voice_id,
        "text": text[:100],  # Truncate for readability
        "timestamp": datetime.now().isoformat(),
    }
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_cache_atomic(cache_file: Path, source_file: Path):
    """Write cache file atomically."""
    temp_file = cache_file.with_suffix(".tmp")
    shutil.copy2(source_file, temp_file)
    temp_file.replace(cache_file)



def _pick_speed_ratio(estimated_natural_ms: int, budget_ms: int) -> float:
    """
    分层语速策略：根据预估自然时长和 budget 选择离散 speed_ratio 档位。

    返回 VolcEngine speed_ratio（0.1~2.0，1.0=正常，保留一位小数）。

    离散档位（保证缓存命中率）：
    - 慢：0.7, 0.8, 0.9
    - 正常：1.0
    - 快：1.1, 1.2, 1.3

    策略：让 TTS 原生控制语速，atempo 只做 ±8% 微调。
    """
    if budget_ms <= 0 or estimated_natural_ms <= 0:
        return 1.0
    # 目标 ratio：自然时长 / budget（>1 需要加速，<1 需要减速）
    ratio = estimated_natural_ms / budget_ms
    # 取整到 0.1 档位
    speed_ratio = round(ratio, 1)
    # 限制在合理范围
    speed_ratio = max(0.7, min(1.3, speed_ratio))
    return speed_ratio


def synthesize_tts_per_segment(
    dub_manifest: DubManifest,
    voice_assignment: Dict[str, Any],
    segments_dir: str,
    temp_dir: str,
    *,
    app_id: str,
    access_key: str,
    resource_id: str = DEFAULT_RESOURCE_ID,
    format: str = DEFAULT_FORMAT,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    language: str = "en-US",
    max_workers: int = 4,
) -> TTSReport:
    """
    Per-segment TTS synthesis for Timeline-First Architecture.

    Each utterance in dub_manifest is synthesized to an individual WAV file.
    No concatenation is performed (that's handled by Mix phase).

    Args:
        dub_manifest: DubManifest object (SSOT for dubbing)
        voice_assignment: Speaker -> {voice_type, params} mapping
        segments_dir: Output directory for per-segment WAVs
        temp_dir: Temporary directory for intermediate files
        app_id: VolcEngine APP ID
        access_key: VolcEngine Access Key
        resource_id: Resource ID
        format: Audio format (mp3/ogg_opus/pcm)
        sample_rate: Sample rate
        language: Language code
        max_workers: Number of concurrent workers

    Returns:
        TTSReport with per-segment synthesis results
    """
    output_dir = Path(segments_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    # Get cache paths
    cache_dir, manifest_path = _get_cache_paths(temp_path)

    segment_reports: List[TTSSegmentReport] = []

    for utt in dub_manifest.utterances:
        utt_id = utt.utt_id
        text = utt.text_en.strip()
        budget_ms = utt.budget_ms
        role_key = str(utt.role_id) if utt.role_id is not None else ""
        max_rate = utt.tts_policy.max_rate
        allow_extend_ms = utt.tts_policy.allow_extend_ms

        # Output file path
        segment_file = output_dir / f"seg_{utt_id}.wav"
        segment_file_raw = temp_path / f"seg_{utt_id}_raw.wav"

        if not text:
            # Empty text - create silent audio
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

        # Get voice configuration from voice_assignment（role_id 为索引键）。
        # 无任何 fallback：未分配 role 或 role.voice_type 缺失 → 标 failed，不偷偷用兜底音色。
        voice_info = voice_assignment["speakers"].get(role_key, {})
        voice_id = voice_info.get("voice_type", "")
        if not voice_id:
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
                    error=f"VolcEngine TTS: no voice_type for role={role_key or '<unassigned>'}",
                )
            )
            continue
        # emotion：只传目标语言支持的（emotions.json 的 lang 字段）
        # 不支持的（如 coldness/hate 只有 zh）不传，走默认语气，避免 API 报错
        raw_emotion = utt.emotion
        tts_lang = "en" if language.startswith("en") else "zh"
        emotion = raw_emotion if raw_emotion and emotion_supports_lang(raw_emotion, tts_lang) else None

        # speed_ratio 暂不传：需要两次试探（先 1.0 合成量测，再决定是否重新合成）

        # cache key: emotion 参与（不同情绪产出不同音频），不支持的 emotion 已被过滤为 None
        prosody = {}
        if emotion:
            prosody["emotion"] = emotion
        cache_key = _generate_cache_key(text, voice_id, prosody, language)
        cache_file = cache_dir / f"{cache_key}.wav"

        try:
            # Check cache
            if cache_file.exists():
                shutil.copy2(cache_file, segment_file_raw)
                print(f"  💾 [{utt_id}] Cache hit")
            else:
                audio_bytes, _ = _call_volcengine_tts(
                    text=text,
                    speaker=voice_id,
                    app_id=app_id,
                    access_key=access_key,
                    resource_id=resource_id,
                    format=format,
                    sample_rate=sample_rate,
                    emotion=emotion,
                )

                # Convert to WAV
                if format == "pcm":
                    temp_pcm = temp_path / f"seg_{utt_id}_temp.pcm"
                    with open(temp_pcm, "wb") as f:
                        f.write(audio_bytes)

                    cmd = [
                        "ffmpeg",
                        "-f", "s16le",
                        "-ar", str(sample_rate),
                        "-ac", str(CACHE_CHANNELS),
                        "-i", str(temp_pcm),
                        "-ar", str(CACHE_SAMPLE_RATE),
                        "-ac", str(CACHE_CHANNELS),
                        "-sample_fmt", "s16",
                        "-y",
                        str(segment_file_raw),
                    ]
                    subprocess.run(cmd, check=True, capture_output=True)
                    temp_pcm.unlink(missing_ok=True)
                else:
                    temp_audio = temp_path / f"seg_{utt_id}_temp.{format}"
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

            # Trim silence (only when raw > budget, otherwise skip)
            trimmed_file = temp_path / f"seg_{utt_id}_trimmed.wav"
            if raw_ms <= budget_ms:
                # Raw fits in budget, skip trimming to avoid cutting speech
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
                        # 音频已经能放进 extended budget，直接 pad 静音，不减速
                        _pad_audio(str(trimmed_file), str(segment_file), extended_budget)
                        final_ms = extended_budget
                        rate = 1.0
                        status = TTSSegmentStatus.EXTENDED
                    elif trimmed_ms / extended_budget <= max_rate:
                        # 需要加速但在 max_rate 范围内
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

            # Cleanup temp files
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
            print(f"  ✅ [{utt_id}] {raw_ms}ms → {trimmed_ms}ms → {final_ms}ms (rate={rate:.2f}x)")

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
            print(f"  ❌ [{utt_id}] Failed: {e}")

    return TTSReport(
        audio_duration_ms=dub_manifest.audio_duration_ms,
        segments_dir=segments_dir,
        segments=segment_reports,
    )


def _get_duration_ms(audio_path: str) -> int:
    """Get audio duration in milliseconds using ffprobe."""
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
    """Pad audio to exact target duration."""
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
    """Apply tempo rate adjustment and pad to target duration."""
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
