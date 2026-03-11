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
    _allow_aggressive_compression,
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


def synthesize_tts(
    en_segments_path: str,
    voice_assignment_path: str,
    voice_pool_path: Optional[str],
    output_dir: str,
    *,
    app_id: str,
    access_key: str,
    resource_id: str = DEFAULT_RESOURCE_ID,
    format: str = DEFAULT_FORMAT,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    language: str = "en-US",  # Not used by VolcEngine, kept for compatibility
    max_workers: int = 4,  # Not used, kept for compatibility
    enable_timestamp: bool = False,  # TTS1.0 支持
    enable_subtitle: bool = False,  # TTS2.0/ICL2.0 支持
) -> tuple[str, List[Dict[str, Any]]]:
    """
    Synthesize TTS for each segment using VolcEngine TTS with episode-level caching.
    
    Args:
        en_segments_path: Path to segments JSON file
        voice_assignment_path: Path to voice_assignment.json
        voice_pool_path: Path to voice pool JSON (None = use default)
        output_dir: Output directory (should be .temp/tts)
        app_id: VolcEngine APP ID
        access_key: VolcEngine Access Key
        resource_id: Resource ID (e.g., "seed-tts-1.0", "seed-tts-2.0")
        format: Audio format (mp3/ogg_opus/pcm)
        sample_rate: Sample rate
        language: Language code (not used by VolcEngine, kept for compatibility)
        max_workers: Number of concurrent workers (not used, kept for compatibility)
        
    Returns:
        Tuple of (path to tts_en.wav, list of sentence data)
        sentence data format: [{"seg_id": int, "text": str, "words": [...], ...}, ...]
    """
    from dubora_pipeline.models.voice_pool import VoicePool
    
    # Load data
    with open(en_segments_path, "r", encoding="utf-8") as f:
        en_segments = json.load(f)
    
    with open(voice_assignment_path, "r", encoding="utf-8") as f:
        voice_assignment = json.load(f)
    
    voice_pool = VoicePool(pool_path=voice_pool_path)
    
    output = Path(output_dir)
    # 保存到 .temp/tts/volcengine/segments 目录
    # output_dir 是 workspace/.temp/tts
    segments_dir = output / "volcengine" / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    
    # Get cache paths
    cache_dir, manifest_path = _get_cache_paths(output)
    
    # Sort segments by start time
    en_segments_sorted = sorted(en_segments, key=lambda x: x["start"])
    
    segment_files = []
    cache_hits = 0
    cache_misses = 0
    
    # Statistics
    speedup_stats = []
    compression_type_counts = {}
    
    # Collect sentence data
    all_sentences = []
    
    for i, seg in enumerate(en_segments_sorted):
        seg_id = seg['id']
        speaker = seg["speaker"]
        text = seg.get("en_text", seg.get("text", "")).strip()
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_duration = seg_end - seg_start
        duration_ms = seg_duration * 1000
        
        # Diagnostics
        print(f"[TTS DIAG] seg_id={seg_id}, start_ms={seg_start*1000:.0f}, end_ms={seg_end*1000:.0f}, duration_ms={duration_ms:.0f}")
        print(f"[TTS DIAG] TEXT: {repr(text)}")
        print(f"[TTS DIAG] DURATION_MS: {duration_ms:.1f}")
        
        # Check if text is empty or contains only punctuation/whitespace
        text_stripped = text.strip()
        if not text_stripped:
            # Empty segment - create silent audio
            print(f"  ⚠️  [{seg_id}] Empty text, creating silent audio")
            segment_file_raw = segments_dir / f"seg_{seg_id:04d}_raw.wav"
            segment_file = segments_dir / f"seg_{seg_id:04d}.wav"
            _create_silent_audio(str(segment_file_raw), seg_duration)
            # Copy to aligned file
            shutil.copy2(segment_file_raw, segment_file)
            segment_files.append((str(segment_file), seg_start, seg_end))
            continue
        
        # Check if text contains only punctuation/whitespace (no actual words)
        import re
        # Remove all punctuation and whitespace, check if anything remains
        text_without_punc = re.sub(r'[^\w\s]', '', text_stripped)
        text_without_punc = re.sub(r'\s+', '', text_without_punc)
        if not text_without_punc:
            # Only punctuation/whitespace - create silent audio
            print(f"  ⚠️  [{seg_id}] Text contains only punctuation/whitespace: {repr(text)}, creating silent audio")
            segment_file_raw = segments_dir / f"seg_{seg_id:04d}_raw.wav"
            segment_file = segments_dir / f"seg_{seg_id:04d}.wav"
            _create_silent_audio(str(segment_file_raw), seg_duration)
            # Copy to aligned file
            shutil.copy2(segment_file_raw, segment_file)
            segment_files.append((str(segment_file), seg_start, seg_end))
            continue
        
        voice_info = voice_assignment["speakers"].get(speaker, {})
        # 优先用 voice_type（speaker_to_role 模式），回退到扁平 voice_id，再回退到嵌套结构
        voice_id = (
            voice_info.get("voice_type")
            or voice_info.get("voice_id")
            or voice_info.get("voice", {}).get("voice_id")
            or "zh_female_shuangkuaisisi_moon_bigtts"
        )

        # Get voice config from pool
        pool_key = voice_info.get("pool_key") or voice_info.get("voice", {}).get("pool_key")
        if pool_key:
            voice_config = voice_pool.get_voice(pool_key)
            prosody = voice_config.get("prosody", {}) if voice_config else {}
        else:
            prosody = {}
        
        # Generate cache key
        cache_key = _generate_cache_key(text, voice_id, prosody, language)
        cache_file = cache_dir / f"{cache_key}.wav"
        segment_file_raw = segments_dir / f"seg_{seg_id:04d}_raw.wav"
        segment_file = segments_dir / f"seg_{seg_id:04d}.wav"
        
        # Check cache
        if cache_file.exists():
            # Cache hit
            shutil.copy2(cache_file, segment_file_raw)
            cache_hits += 1
            print(f"  💾 [{seg_id}] Cache hit: {text[:50]}...")
            
            # Diagnostics
            if AUDIO_DIAGNOSTICS_AVAILABLE and segment_file_raw.exists():
                try:
                    audio, sr = sf.read(str(segment_file_raw))
                    rms = np.sqrt(np.mean(audio**2)) if len(audio) > 0 else 0.0
                    print(f"[TTS DIAG] AUDIO (from cache): dtype={audio.dtype}, shape={audio.shape}, min={audio.min():.6f}, max={audio.max():.6f}, RMS={rms:.6f}")
                except Exception as e:
                    print(f"[TTS DIAG] Failed to read cached audio for diagnostics: {e}")
        else:
            # Cache miss - synthesize
            cache_misses += 1
            
            # Calculate speech rate from prosody (Azure uses rate, VolcEngine uses speech_rate: -50 to 100)
            # Azure rate: 0.5-2.0, VolcEngine: -50 to 100 (0 = normal, 100 = 2.0×)
            azure_rate = prosody.get("rate", 1.0)
            if azure_rate <= 0.5:
                speech_rate = -50  # 0.5×
            elif azure_rate >= 2.0:
                speech_rate = 100  # 2.0×
            else:
                # Linear mapping: 1.0 -> 0, 0.5 -> -50, 2.0 -> 100
                speech_rate = (azure_rate - 1.0) * 100
            
            # Get emotion from segment
            emotion = seg.get("emotion")
            
            try:
                # Call VolcEngine TTS API
                print(f"  🎤 [{seg_id}] Calling VolcEngine TTS API for text: {text[:50]}...")
                audio_bytes, sentence_data = _call_volcengine_tts(
                    text=text,
                    speaker=voice_id,
                    app_id=app_id,
                    access_key=access_key,
                    resource_id=resource_id,
                    format=format,
                    sample_rate=sample_rate,
                    speech_rate=speech_rate,
                    emotion=emotion,
                    enable_timestamp=enable_timestamp,
                    enable_subtitle=enable_subtitle,
                )
                print(f"  🎤 [{seg_id}] Received {len(audio_bytes)} bytes from VolcEngine TTS API")
                
                # Collect sentence data if available
                if sentence_data:
                    all_sentences.append({
                        "seg_id": seg_id,
                        "text": text,
                        "sentence": sentence_data,
                    })
                
                # Save raw audio (format depends on API response)
                if format == "pcm":
                    # PCM: save as raw PCM, then convert to WAV
                    # Note: VolcEngine PCM is 16-bit signed little-endian, mono
                    temp_pcm = segments_dir / f"seg_{seg_id:04d}_temp.pcm"
                    with open(temp_pcm, "wb") as f:
                        f.write(audio_bytes)
                    
                    # Calculate expected duration for PCM
                    # PCM: 16-bit = 2 bytes per sample, mono = 1 channel
                    bytes_per_sample = 2
                    samples = len(audio_bytes) // bytes_per_sample
                    expected_duration_sec = samples / sample_rate
                    print(f"  🎵 [{seg_id}] PCM audio: {len(audio_bytes)} bytes, {samples} samples, expected duration: {expected_duration_sec:.3f}s at {sample_rate}Hz")
                    
                    # Convert PCM to WAV using ffmpeg with explicit format
                    cmd = [
                        "ffmpeg",
                        "-f", "s16le",  # 16-bit signed little-endian PCM
                        "-ar", str(sample_rate),
                        "-ac", str(CACHE_CHANNELS),  # mono
                        "-i", str(temp_pcm),
                        "-ar", str(CACHE_SAMPLE_RATE),
                        "-ac", str(CACHE_CHANNELS),
                        "-sample_fmt", "s16",
                        "-y",
                        str(segment_file_raw),
                    ]
                    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                    temp_pcm.unlink(missing_ok=True)
                    
                    # Check actual duration of converted WAV
                    if segment_file_raw.exists():
                        check_cmd = [
                            "ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(segment_file_raw)
                        ]
                        check_result = subprocess.run(check_cmd, capture_output=True, text=True, check=True)
                        duration_str = check_result.stdout.strip()
                        if duration_str == "N/A" or not duration_str:
                            # 如果 ffprobe 返回 N/A 或空，说明文件无效，跳过打印
                            pass
                        else:
                            actual_duration = float(duration_str)
                            print(f"  🎵 [{seg_id}] Converted WAV duration: {actual_duration:.3f}s")
                else:
                    # MP3/OGG: save directly, then convert to WAV
                    temp_audio = segments_dir / f"seg_{seg_id:04d}_temp.{format}"
                    with open(temp_audio, "wb") as f:
                        f.write(audio_bytes)
                    
                    # Convert to WAV (ffmpeg will auto-detect format)
                    _normalize_audio_format(
                        str(temp_audio),
                        str(segment_file_raw),
                        sample_rate=sample_rate,
                        channels=CACHE_CHANNELS,
                    )
                    temp_audio.unlink(missing_ok=True)
                
                # Diagnostics
                if AUDIO_DIAGNOSTICS_AVAILABLE and segment_file_raw.exists():
                    try:
                        audio, sr = sf.read(str(segment_file_raw))
                        rms = np.sqrt(np.mean(audio**2)) if len(audio) > 0 else 0.0
                        print(f"[TTS DIAG] AUDIO (after synthesis): dtype={audio.dtype}, shape={audio.shape}, min={audio.min():.6f}, max={audio.max():.6f}, RMS={rms:.6f}")
                    except Exception as e:
                        print(f"[TTS DIAG] Failed to read audio for diagnostics: {e}")
                
                # Write to cache (atomic)
                _write_cache_atomic(cache_file, segment_file_raw)
                
            except Exception as e:
                print(f"Warning: TTS failed for segment {seg_id}: {e}")
                # Create silent segment as fallback
                _create_silent_audio(str(segment_file_raw), seg_duration)
        
        # Align segment to window
        next_seg = en_segments_sorted[i + 1] if i + 1 < len(en_segments_sorted) else None
        next_seg_start_ms = next_seg.get("start") * 1000.0 if next_seg else None
        current_seg_start_ms = seg_start * 1000.0
        
        # 打印每句音频的保存位置和时长信息
        print(f"  💾 [{seg_id}] Raw audio saved: {segment_file_raw}")
        print(f"  💾 [{seg_id}] Aligned audio will be saved: {segment_file}")
        print(f"  ⏱️  [{seg_id}] Target duration: {duration_ms:.0f}ms, Text: {text[:50]}...")
        if next_seg:
            gap_to_next = (next_seg.get("start") * 1000.0) - (seg_end * 1000.0)
            print(f"  ⏱️  [{seg_id}] Gap to next segment: {gap_to_next:.0f}ms")
        
        seg_stats: Dict[str, Any] = {}
        
        _align_segment_to_window(
            str(segment_file_raw),
            str(segment_file),
            duration_ms,
            text=text,
            next_seg_start_ms=next_seg_start_ms,
            current_seg_start_ms=current_seg_start_ms,
            stats=seg_stats,
        )
        
        # Record statistics
        if "speedup" in seg_stats:
            speedup_stats.append(seg_stats)
            comp_type = seg_stats.get("compression_type", "unknown")
            compression_type_counts[comp_type] = compression_type_counts.get(comp_type, 0) + 1
        
        # Print segment statistics
        if "original_duration_ms" in seg_stats:
            original_ms = seg_stats["original_duration_ms"]
            trimmed_ms = seg_stats.get("trimmed_duration_ms", original_ms)
            final_ms = duration_ms
            compression_type = seg_stats.get("compression_type", "none")
            speedup = seg_stats.get("speedup", 1.0)
            print(f"  📏 [{seg_id}] Duration: {original_ms:.0f}ms (raw) -> {trimmed_ms:.0f}ms (trimmed) -> {final_ms:.0f}ms (final)")
            if compression_type == "hard_cut":
                print(f"  ⚠️  [{seg_id}] WARNING: Audio was HARD CUT (truncated)! Original: {original_ms:.0f}ms, Target: {final_ms:.0f}ms")
            elif compression_type in ["aggressive", "aggressive_max"]:
                print(f"  ⚠️  [{seg_id}] WARNING: Audio was aggressively compressed ({speedup:.2f}× speedup)")
        
        # Diagnostics
        if AUDIO_DIAGNOSTICS_AVAILABLE and segment_file.exists():
            try:
                audio, sr = sf.read(str(segment_file))
                rms = np.sqrt(np.mean(audio**2)) if len(audio) > 0 else 0.0
                actual_duration = len(audio) / sr if sr > 0 else 0.0
                print(f"[TTS DIAG] AUDIO (after align): dtype={audio.dtype}, shape={audio.shape}, duration={actual_duration:.3f}s, min={audio.min():.6f}, max={audio.max():.6f}, RMS={rms:.6f}")
            except Exception as e:
                print(f"[TTS DIAG] Failed to read aligned audio for diagnostics: {e}")
        
        segment_files.append((str(segment_file), seg_start, seg_end))
        
        # Append to manifest
        _append_manifest(manifest_path, seg_id, cache_key, voice_id, text)
    
    # Print cache statistics
    total = cache_hits + cache_misses
    if total > 0:
        hit_rate = (cache_hits / total) * 100
        print(f"  📊 Cache: {cache_hits}/{total} hits ({hit_rate:.1f}%)")
    
    # Print compression statistics
    if speedup_stats and AUDIO_DIAGNOSTICS_AVAILABLE:
        speedup_values = [s.get("speedup", 1.0) if isinstance(s, dict) else s for s in speedup_stats]
        speedup_array = np.array(speedup_values)
        p50 = np.percentile(speedup_array, 50)
        p90 = np.percentile(speedup_array, 90)
        p99 = np.percentile(speedup_array, 99)
        print(f"  📊 Speedup stats: P50={p50:.2f}×, P90={p90:.2f}×, P99={p99:.2f}×")
        print(f"  📊 Compression types: {compression_type_counts}")
        
        aggressive_count = compression_type_counts.get("aggressive", 0) + compression_type_counts.get("aggressive_max", 0)
        aggressive_pct = (aggressive_count / len(speedup_stats)) * 100 if speedup_stats else 0
        if aggressive_pct > 5:
            print(f"  ⚠️  Warning: Aggressive compression rate is {aggressive_pct:.1f}% (>5%), consider adjusting upstream (segmentation/TTS speed)")
        
        total_saved_ms = sum(s.get("silence_saved_ms", 0) for s in speedup_stats if isinstance(s, dict))
        if total_saved_ms > 0:
            avg_saved_ms = total_saved_ms / len(speedup_stats)
            print(f"  📊 Silence trimming: avg saved {avg_saved_ms:.0f}ms per segment (total {total_saved_ms:.0f}ms saved)")
    
    # Concatenate with gaps
    tts_output = output / "tts_en.wav"
    _concatenate_with_gaps(segment_files, str(tts_output))
    
    # 打印每句音频的保存位置总结
    print(f"\n  📁 Segment audio files saved in: {segments_dir}")
    print(f"  📁 Raw audio files: seg_XXXX_raw.wav (original from VolcEngine)")
    print(f"  📁 Aligned audio files: seg_XXXX.wav (after alignment/compression)")
    print(f"  📁 Final concatenated audio: {tts_output}")
    
    return str(tts_output), all_sentences


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
        speaker = utt.speaker
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

        # Get voice configuration from voice_assignment
        voice_info = voice_assignment["speakers"].get(speaker, {})
        voice_id = voice_info.get("voice_type", "zh_female_shuangkuaisisi_moon_bigtts")
        params = voice_info.get("params", {})
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
