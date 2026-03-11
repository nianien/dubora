"""
TTS Synthesis: Azure Neural TTS per segment with episode-level caching.

Functions:
- synthesize_tts: Original function (concatenates to tts_en.wav)
- synthesize_tts_per_segment: New function (per-segment WAVs, no concatenation)
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from dubora_pipeline.schema.dub_manifest import DubManifest
from dubora_pipeline.schema.tts_report import TTSReport, TTSSegmentReport, TTSSegmentStatus

# For audio diagnostics
try:
    import numpy as np
    import soundfile as sf
    AUDIO_DIAGNOSTICS_AVAILABLE = True
except ImportError:
    AUDIO_DIAGNOSTICS_AVAILABLE = False

# Cache configuration
CACHE_ENGINE = "azure"
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
        voice_id: Azure voice ID (e.g., "en-US-JennyNeural")
        prosody: Prosody settings (rate, pitch, style, etc.)
        language: Language code (e.g., "en-US")
        
    Returns:
        SHA256 hash (hex string)
    """
    # Normalize text
    text_norm = _normalize_text(text)
    
    # Build payload
    payload = {
        "engine": CACHE_ENGINE,
        "engine_ver": CACHE_ENGINE_VER,
        "voice": voice_id,
        "lang": language,
        "format": CACHE_FORMAT,
        "sample_rate": CACHE_SAMPLE_RATE,
        "channels": CACHE_CHANNELS,
        "prosody": {
            "rate": prosody.get("rate", 1.0),
            "pitch": prosody.get("pitch", 0),
            "style": prosody.get("style", "general"),
            "role": prosody.get("role", ""),
            "volume": prosody.get("volume", ""),
        },
        "text": text_norm,
    }
    
    # Generate SHA256 hash
    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    cache_key = hashlib.sha256(payload_json.encode('utf-8')).hexdigest()
    
    return cache_key


def _get_cache_paths(output_dir: Path) -> tuple[Path, Path]:
    """
    Get cache directory and manifest path.
    
    Returns:
        (cache_dir, manifest_path)
    """
    cache_dir = output_dir / CACHE_ENGINE
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.jsonl"
    return cache_dir, manifest_path


def _write_cache_atomic(cache_file: Path, source_file: Path):
    """
    Atomically write cache file (write to .tmp first, then rename).
    
    Args:
        cache_file: Final cache file path
        source_file: Source file to copy
    """
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = cache_file.with_suffix('.tmp')
    
    # Copy to temp file
    shutil.copy2(source_file, temp_file)
    
    # Atomic rename
    temp_file.replace(cache_file)


def _append_manifest(
    manifest_path: Path,
    seg_id: int,
    cache_key: str,
    voice_id: str,
    text: str,
):
    """
    Append entry to manifest.jsonl.
    
    Args:
        manifest_path: Path to manifest.jsonl
        seg_id: Segment ID
        cache_key: Cache key (SHA256)
        voice_id: Voice ID
        text: Original text (for debugging)
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Generate text digest
    text_digest = hashlib.sha1(text.encode('utf-8')).hexdigest()
    
    entry = {
        "seg": seg_id,
        "key": cache_key,
        "voice": voice_id,
        "text_sha1": text_digest,
        "ts": datetime.now().isoformat(),
    }
    
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _normalize_audio_format(
    input_path: str,
    output_path: str,
    sample_rate: int = CACHE_SAMPLE_RATE,
    channels: int = CACHE_CHANNELS,
):
    """
    Normalize audio to specified format using ffmpeg.
    
    Args:
        input_path: Input audio file
        output_path: Output audio file
        sample_rate: Target sample rate
        channels: Target channels (1=mono, 2=stereo)
    """
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-sample_fmt", "s16",  # 16-bit PCM
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def synthesize_tts(
    en_segments_path: str,
    voice_assignment_path: str,
    voice_pool_path: Optional[str],
    output_dir: str,
    *,
    azure_key: str,
    azure_region: str,
    language: str = "en-US",
    max_workers: int = 4,
) -> str:
    """
    Synthesize TTS for each segment using Azure Neural TTS with episode-level caching.
    
    Args:
        en_segments_path: Path to segments JSON file (临时文件，由 processor 创建)
        voice_assignment_path: Path to voice_assignment.json
        voice_pool_path: Path to voice pool JSON (None = use default)
        output_dir: Output directory (should be .temp/tts)
        azure_key: Azure Speech Service key
        azure_region: Azure Speech Service region
        language: TTS language
        max_workers: Number of concurrent workers (not used in v1, kept for compatibility)
        
    Returns:
        Path to tts_en.wav
    """
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        raise ImportError(
            "azure-cognitiveservices-speech is not installed. "
            "Install it with: pip install azure-cognitiveservices-speech"
        )
    
    from dubora_pipeline.models.voice_pool import VoicePool
    
    # Load data
    with open(en_segments_path, "r", encoding="utf-8") as f:
        en_segments = json.load(f)
    
    with open(voice_assignment_path, "r", encoding="utf-8") as f:
        voice_assignment = json.load(f)
    
    voice_pool = VoicePool(pool_path=voice_pool_path)
    
    # Initialize Azure Speech
    speech_config = speechsdk.SpeechConfig(
        subscription=azure_key,
        region=azure_region,
    )
    speech_config.speech_synthesis_language = language
    
    # Set output format to 24kHz mono PCM (WAV)
    # Note: Azure SDK doesn't directly support WAV, so we'll convert from MP3
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    
    output = Path(output_dir)
    # 保存到 .temp/tts/azure/segments 目录
    segments_dir = output / "azure" / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    
    # Get cache paths
    cache_dir, manifest_path = _get_cache_paths(output)
    
    # v1 整改：合成每个 segment 后立刻做段内对齐，在 concat 前插入 gap 静音段
    # 排序 segments 按 start 时间
    en_segments_sorted = sorted(en_segments, key=lambda x: x["start"])
    
    segment_files = []
    cache_hits = 0
    cache_misses = 0
    
    # 统计信息
    speedup_stats = []  # 记录每个 segment 的 speedup
    compression_type_counts = {}  # 记录压缩类型分布
    
    for i, seg in enumerate(en_segments_sorted):
        seg_id = seg['id']
        speaker = seg["speaker"]
        # 支持两种字段名：en_text（向后兼容）和 text（新格式）
        text = seg.get("en_text", seg.get("text", "")).strip()
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_duration = seg_end - seg_start
        duration_ms = seg_duration * 1000  # 转换为毫秒
        
        # 诊断信息 1: 合成前检查
        print(f"[TTS DIAG] seg_id={seg_id}, start_ms={seg_start*1000:.0f}, end_ms={seg_end*1000:.0f}, duration_ms={duration_ms:.0f}")
        print(f"[TTS DIAG] TEXT: {repr(text)}")
        print(f"[TTS DIAG] DURATION_MS: {duration_ms:.1f}")
        
        # 断言：文本不能为空
        assert text.strip(), f"Empty text for segment {seg_id}"
        
        if not text:
            # Empty segment - create silent audio of exact duration
            segment_file = segments_dir / f"seg_{seg_id:04d}.wav"
            _create_silent_audio(str(segment_file), seg_duration)
            segment_files.append((str(segment_file), seg_start, seg_end))
            continue
        
        voice_info = voice_assignment["speakers"].get(speaker, {})
        voice_id = voice_info.get("voice", {}).get("voice_id", "en-US-JennyNeural")
        
        # Get voice config from pool
        pool_key = voice_info.get("voice", {}).get("pool_key")
        if pool_key:
            voice_config = voice_pool.get_voice(pool_key)
            prosody = voice_config.get("prosody", {})
        else:
            prosody = {}
        
        # Generate cache key
        cache_key = _generate_cache_key(text, voice_id, prosody, language)
        cache_file = cache_dir / f"{cache_key}.wav"
        segment_file_raw = segments_dir / f"seg_{seg_id:04d}_raw.wav"
        segment_file = segments_dir / f"seg_{seg_id:04d}.wav"
        
        # Check cache
        if cache_file.exists():
            # Cache hit - copy to raw segment file
            shutil.copy2(cache_file, segment_file_raw)
            cache_hits += 1
            print(f"  💾 [{seg_id}] Cache hit: {text[:50]}...")
            
            # 诊断信息 2a: 缓存命中后也检查音频
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
            
            # Set voice
            speech_config.speech_synthesis_voice_name = voice_id
            
            # Create temporary file for Azure output (Azure outputs MP3 by default)
            temp_azure_output = segments_dir / f"seg_{seg_id:04d}_azure.mp3"
            audio_config = speechsdk.audio.AudioOutputConfig(filename=str(temp_azure_output))
            
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config,
                audio_config=audio_config,
            )
            
            # Build SSML with prosody
            ssml = f"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{language}">
    <voice name="{voice_id}">
        <prosody rate="{prosody.get('rate', 1.0)}" pitch="{prosody.get('pitch', 0)}%">
            {text}
        </prosody>
    </voice>
</speak>"""
            
            try:
                result = synthesizer.speak_ssml_async(ssml).get()
                
                if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    # Normalize to WAV 24k mono PCM
                    _normalize_audio_format(
                        str(temp_azure_output),
                        str(segment_file_raw),
                        sample_rate=CACHE_SAMPLE_RATE,
                        channels=CACHE_CHANNELS,
                    )
                    
                    # 诊断信息 2: normalize 后检查音频
                    if AUDIO_DIAGNOSTICS_AVAILABLE and segment_file_raw.exists():
                        try:
                            audio, sr = sf.read(str(segment_file_raw))
                            rms = np.sqrt(np.mean(audio**2)) if len(audio) > 0 else 0.0
                            print(f"[TTS DIAG] AUDIO (after normalize): dtype={audio.dtype}, shape={audio.shape}, min={audio.min():.6f}, max={audio.max():.6f}, RMS={rms:.6f}")
                        except Exception as e:
                            print(f"[TTS DIAG] Failed to read audio for diagnostics: {e}")
                    
                    # Write to cache (atomic)
                    _write_cache_atomic(cache_file, segment_file_raw)
                    
                    # Clean up temp Azure output
                    temp_azure_output.unlink(missing_ok=True)
                else:
                    cancellation_details = speechsdk.CancellationDetails(result)
                    error_msg = f"{result.reason}"
                    if cancellation_details.reason == speechsdk.CancellationReason.Error:
                        error_msg += f": {cancellation_details.error_details}"
                    print(f"Warning: TTS failed for segment {seg_id}: {error_msg}")
                    # Create silent segment as fallback
                    _create_silent_audio(str(segment_file_raw), seg_duration)
                    temp_azure_output.unlink(missing_ok=True)
            except Exception as e:
                print(f"Warning: TTS exception for segment {seg_id}: {e}")
                # Create silent segment as fallback
                _create_silent_audio(str(segment_file_raw), seg_duration)
                temp_azure_output.unlink(missing_ok=True)
        
        # v1 整改：合成每个 segment 后立刻做段内对齐
        # 获取下一句的信息（用于检查重叠）
        next_seg = en_segments_sorted[i + 1] if i + 1 < len(en_segments_sorted) else None
        next_seg_start_ms = next_seg.get("start") * 1000.0 if next_seg else None
        current_seg_start_ms = seg_start * 1000.0
        
        # 统计信息
        seg_stats: Dict[str, Any] = {}
        
        _align_segment_to_window(
            str(segment_file_raw),
            str(segment_file),
            duration_ms,  # budget_ms（毫秒）
            text=text,
            next_seg_start_ms=next_seg_start_ms,
            current_seg_start_ms=current_seg_start_ms,
            stats=seg_stats,
        )
        
        # 记录统计信息
        if "speedup" in seg_stats:
            speedup_stats.append(seg_stats)
            comp_type = seg_stats.get("compression_type", "unknown")
            compression_type_counts[comp_type] = compression_type_counts.get(comp_type, 0) + 1
        
        # 打印每个 segment 的详细统计（raw_duration / trimmed_duration / final_duration）
        if "original_duration_ms" in seg_stats:
            original_ms = seg_stats["original_duration_ms"]
            trimmed_ms = seg_stats.get("trimmed_duration_ms", original_ms)
            final_ms = duration_ms  # budget_ms
            print(f"  📏 [{seg_id}] Duration: {original_ms:.0f}ms (raw) -> {trimmed_ms:.0f}ms (trimmed) -> {final_ms:.0f}ms (final)")
        
        # 诊断信息 3: align 后检查音频
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
        # 提取 speedup 值
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
        
        # Print silence trimming statistics
        total_saved_ms = sum(s.get("silence_saved_ms", 0) for s in speedup_stats if isinstance(s, dict))
        if total_saved_ms > 0:
            avg_saved_ms = total_saved_ms / len(speedup_stats)
            print(f"  📊 Silence trimming: avg saved {avg_saved_ms:.0f}ms per segment (total {total_saved_ms:.0f}ms saved)")
    
    # v1 整改：在 concat 前插入 gap 静音段
    tts_output = output / "tts_en.wav"
    _concatenate_with_gaps(segment_files, str(tts_output))
    
    return str(tts_output)


def _create_silent_audio(output_path: str, duration: float):
    """Create silent audio file of specified duration in WAV 24k mono PCM format."""
    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", f"anullsrc=r={CACHE_SAMPLE_RATE}:cl=mono",
        "-t", str(duration),
        "-ar", str(CACHE_SAMPLE_RATE),
        "-ac", str(CACHE_CHANNELS),
        "-sample_fmt", "s16",
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _allow_aggressive_compression(text: str) -> bool:
    """
    判断是否允许使用 aggressive 压缩（>1.25×）。
    
    允许条件（短词/拟声词）：
    - word_count <= 3
    - 包含拟声词（ha/haha/ah/oh）
    - 短口头语
    
    Args:
        text: 文本内容
    
    Returns:
        True 如果允许 aggressive 压缩
    """
    if not text:
        return False
    
    # 计算单词数
    words = text.split()
    word_count = len(words)
    
    # 条件 1: 单词数 <= 3
    if word_count <= 3:
        return True
    
    # 条件 2: 包含拟声词/笑声
    text_lower = text.lower()
    interjections = ["ha", "haha", "ah", "oh", "hey", "bro", "wow", "yeah", "ok", "okay"]
    for interj in interjections:
        if interj in text_lower:
            return True
    
    return False


def _trim_silence(
    input_path: str,
    output_path: str,
    threshold_db: float = -40.0,
    min_silence_ms: float = 50.0,
) -> tuple[float, float]:
    """
    去除音频首尾静音（只裁静音，不裁语音）。
    
    重要：这是第一步，必须在判断是否超长之前执行。
    
    策略：
    - 只裁首尾连续静音，不动中间停顿
    - 阈值：-40 dBFS（工程安全标准）
    - 最小连续静音长度：50 ms（避免误裁）
    
    Args:
        input_path: 输入音频文件
        output_path: 输出音频文件（去除静音后）
        threshold_db: 静音阈值（dBFS），默认 -40 dB
        min_silence_ms: 最小连续静音长度（毫秒），默认 50 ms
    
    Returns:
        (trimmed_duration_sec, saved_ms) 元组：
        - trimmed_duration_sec: 去除静音后的实际时长（秒）
        - saved_ms: 节省的时长（毫秒）
    """
    import subprocess
    
    # 获取原始时长
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True,
        text=True,
        check=True,
    )
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        # 如果 ffprobe 返回 N/A 或空，说明文件无效，返回默认值
        original_duration = 0.0
    else:
        original_duration = float(duration_str)
    
    # 使用 silenceremove 去除首尾静音
    # start_periods=1: 只检测开头的一段静音
    # stop_periods=1: 只检测结尾的一段静音
    # start_duration / stop_duration: 最小连续静音长度（秒）
    # start_threshold / stop_threshold: 静音阈值（dB）
    # detection=peak: 使用峰值检测（更准确）
    min_silence_sec = min_silence_ms / 1000.0
    filter_str = (
        f"silenceremove="
        f"start_periods=1:"
        f"start_duration={min_silence_sec}:"
        f"start_threshold={threshold_db}dB:"
        f"detection=peak:"
        f"stop_periods=-1:"
        f"stop_duration={min_silence_sec}:"
        f"stop_threshold={threshold_db}dB"
    )
    
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-af", filter_str,
        "-ar", str(CACHE_SAMPLE_RATE),
        "-ac", str(CACHE_CHANNELS),
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    
    # 获取去除静音后的时长
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", output_path],
        capture_output=True,
        text=True,
        check=True,
    )
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        # 如果 ffprobe 返回 N/A 或空，说明文件无效，使用原始时长
        trimmed_duration = original_duration
    else:
        trimmed_duration = float(duration_str)
    
    saved_ms = (original_duration - trimmed_duration) * 1000.0
    
    return trimmed_duration, saved_ms


def _align_segment_to_window(
    input_path: str,
    output_path: str,
    budget_ms: float,  # 目标时长（毫秒）
    text: Optional[str] = None,
    next_seg_start_ms: Optional[float] = None,
    current_seg_start_ms: Optional[float] = None,
    stats: Optional[Dict[str, Any]] = None,  # 用于统计
):
    """
    将音频对齐到时间窗口（分级压缩 + 允许溢出 + 最后截断）。
    
    决策顺序：
    1. Trim 静音
    2. 如果 real_ms <= budget_ms → padding
    3. 如果超窗但不重叠 → 放行（不压缩，不截断）
    4. 需要压缩时：safe (1.25×) → aggressive (1.6× 或 2×，需触发条件)
    5. 压到极限仍不够 → 截断（加淡出）
    
    Args:
        input_path: 原始音频文件
        output_path: 对齐后的音频文件
        budget_ms: 目标时长（毫秒）
        text: 文本内容（用于判断是否允许 aggressive 压缩）
        next_seg_start_ms: 下一句的开始时间（毫秒），用于检查重叠
        current_seg_start_ms: 当前句的开始时间（毫秒），用于检查重叠
        stats: 统计字典（用于记录 speedup 等信息）
    """
    import subprocess
    
    budget_sec = budget_ms / 1000.0
    
    # Step 0: Trim 静音（必须是第一步，在判断是否超长之前）
    # 注意：如果原始音频比 budget 短，不需要 trim 静音，直接使用原始音频
    # 因为 trim 可能会过度裁剪，导致音频内容丢失
    temp_trimmed = input_path + ".trimmed.wav"
    
    # 先检查原始音频时长
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True,
        text=True,
        check=True,
    )
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        original_duration_sec = 0.0
    else:
        original_duration_sec = float(duration_str)
    original_ms = original_duration_sec * 1000.0
    
    # 如果原始音频比 budget 短，不 trim 静音，直接使用原始音频
    if original_ms <= budget_ms:
        # 原始音频比 budget 短，不需要 trim，直接使用原始音频
        real_sec = original_duration_sec
        real_ms = original_ms
        saved_ms = 0.0
        # 直接使用原始文件，不需要 trim
        shutil.copy2(input_path, temp_trimmed)
    else:
        # 原始音频比 budget 长，需要 trim 静音
        real_sec, saved_ms = _trim_silence(input_path, temp_trimmed)
        real_ms = real_sec * 1000.0
    
    # 记录原始时长（用于统计）
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True,
        text=True,
        check=True,
    )
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        # 如果 ffprobe 返回 N/A 或空，说明文件无效，使用 trimmed 时长
        original_duration_sec = real_sec
    else:
        original_duration_sec = float(duration_str)
    original_ms = original_duration_sec * 1000.0
    
    # 打印统计信息
    if saved_ms > 10:  # 只打印有意义的节省
        print(f"  ✂️  Trimmed silence: {original_ms:.0f}ms -> {real_ms:.0f}ms (saved {saved_ms:.0f}ms)")
    
    if stats is not None:
        stats["original_duration_ms"] = original_ms
        stats["trimmed_duration_ms"] = real_ms
        stats["silence_saved_ms"] = saved_ms
    
    # 计算需要的压缩比（ratio < 1 表示需要加速）
    ratio = budget_ms / real_ms if real_ms > 0 else 1.0
    speedup = 1.0 / ratio if ratio > 0 else 1.0  # speedup > 1 表示加速
    
    # Step 1: 如果 real_ms <= budget_ms → padding
    if real_ms <= budget_ms:
        pad_duration = budget_ms - real_ms
        # 注意：不要使用 -t 参数，因为 apad 会自动 padding 到指定时长
        # 使用 -t 会强制截断，导致原始音频被截断
        cmd = [
            "ffmpeg",
            "-i", temp_trimmed,
            "-af", f"apad=pad_dur={pad_duration/1000.0}",
            "-ar", str(CACHE_SAMPLE_RATE),
            "-ac", str(CACHE_CHANNELS),
            "-y",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        Path(temp_trimmed).unlink(missing_ok=True)
        if stats is not None:
            stats["speedup"] = 1.0
            stats["compression_type"] = "padding"
        return
    
    # Step 2: 如果超窗但不重叠 → 放行（不压缩，不截断）
    if next_seg_start_ms is not None and current_seg_start_ms is not None:
        # 计算当前句的实际结束时间（如果放行）
        actual_end_ms = current_seg_start_ms + real_ms
        if actual_end_ms <= next_seg_start_ms:
            # 不重叠，直接放行（保留完整音频，不截断）
            print(f"  ✅ Overflow allowed: {real_ms:.0f}ms > {budget_ms:.0f}ms, but no overlap with next segment (ends at {actual_end_ms:.0f}ms, next starts at {next_seg_start_ms:.0f}ms)")
            shutil.copy2(temp_trimmed, output_path)
            Path(temp_trimmed).unlink(missing_ok=True)
            if stats is not None:
                stats["speedup"] = 1.0
                stats["compression_type"] = "overflow_allowed"
            return
        else:
            # 会重叠，需要处理
            overlap_ms = actual_end_ms - next_seg_start_ms
            print(f"  ⚠️  Would overlap: {real_ms:.0f}ms > {budget_ms:.0f}ms, would overlap by {overlap_ms:.0f}ms (ends at {actual_end_ms:.0f}ms, next starts at {next_seg_start_ms:.0f}ms)")
    
    # Step 3: 需要压缩时，分级处理
    # safe: ratio >= 0.80 (speedup <= 1.25×)
    # aggressive: ratio >= 0.625 (speedup <= 1.6×) 或 ratio >= 0.50 (speedup <= 2×，需触发条件)
    
    if ratio >= 0.80:
        # Safe 压缩（1.25× 以内）
        compression_ratio = ratio
        compression_type = "safe"
    elif ratio >= 0.625:
        # 可以尝试 1.6×
        compression_ratio = ratio
        compression_type = "moderate"
    elif ratio >= 0.50 and _allow_aggressive_compression(text or ""):
        # Aggressive 压缩（2×，但需要触发条件）
        compression_ratio = ratio
        compression_type = "aggressive"
    else:
        # 压到极限仍不够 → 截断（加淡出）
        # 先尝试最大允许的压缩（如果不允许 aggressive，则用 1.6×）
        if _allow_aggressive_compression(text or ""):
            compression_ratio = 0.50  # 2×
            compression_type = "aggressive_max"
        else:
            compression_ratio = 0.625  # 1.6×
            compression_type = "moderate_max"
        
        # 应用压缩后如果仍超窗，则截断
        compressed_duration = real_sec * compression_ratio
        if compressed_duration > budget_sec:
            # 需要截断（加淡出）
            cmd = [
                "ffmpeg",
                "-i", temp_trimmed,
                "-af", f"atempo={1.0/compression_ratio},afade=t=out:st={budget_sec-0.1}:d=0.1",
                "-t", str(budget_sec),
                "-ar", str(CACHE_SAMPLE_RATE),
                "-ac", str(CACHE_CHANNELS),
                "-y",
                output_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            Path(temp_trimmed).unlink(missing_ok=True)
            if stats is not None:
                stats["speedup"] = 1.0 / compression_ratio
                stats["compression_type"] = "hard_cut"
            print(f"Warning: Segment too long ({real_ms:.0f}ms > {budget_ms:.0f}ms), hard cut with fade")
            return
    
    # 应用压缩
    speedup_ratio = 1.0 / compression_ratio
    # atempo supports 0.5-2.0, chain for larger ratios
    if speedup_ratio < 0.5:
        ratios = []
        remaining = speedup_ratio
        while remaining < 0.5:
            ratios.append(0.5)
            remaining /= 0.5
        ratios.append(remaining)
        filter_str = ",".join([f"atempo={r}" for r in ratios])
    elif speedup_ratio > 2.0:
        ratios = []
        remaining = speedup_ratio
        while remaining > 2.0:
            ratios.append(2.0)
            remaining /= 2.0
        ratios.append(remaining)
        filter_str = ",".join([f"atempo={r}" for r in ratios])
    else:
        filter_str = f"atempo={speedup_ratio}"
    
    # 计算压缩后的实际时长
    compressed_duration_sec = real_sec * compression_ratio
    compressed_duration_ms = compressed_duration_sec * 1000.0
    
    # 如果压缩后音频比 budget 短，需要 padding；如果比 budget 长，需要截断
    if compressed_duration_sec <= budget_sec:
        # 压缩后音频比 budget 短，先压缩，然后 padding 到 budget
        pad_duration_ms = budget_ms - compressed_duration_ms
        filter_str_with_pad = f"{filter_str},apad=pad_dur={pad_duration_ms/1000.0}"
        final_duration = budget_sec
        cmd = [
            "ffmpeg",
            "-i", temp_trimmed,
            "-af", filter_str_with_pad,
            "-t", str(final_duration),
            "-ar", str(CACHE_SAMPLE_RATE),
            "-ac", str(CACHE_CHANNELS),
            "-y",
            output_path,
        ]
    else:
        # 压缩后音频仍比 budget 长，需要截断（加淡出）
        filter_str_with_fade = f"{filter_str},afade=t=out:st={budget_sec-0.1}:d=0.1"
        final_duration = budget_sec
        cmd = [
            "ffmpeg",
            "-i", temp_trimmed,
            "-af", filter_str_with_fade,
            "-t", str(final_duration),
            "-ar", str(CACHE_SAMPLE_RATE),
            "-ac", str(CACHE_CHANNELS),
            "-y",
            output_path,
        ]
    
    if compression_type in ["aggressive", "aggressive_max"]:
        print(f"Info: Segment compressed aggressively ({real_ms:.0f}ms -> {compressed_duration_ms:.0f}ms (compressed) -> {budget_ms:.0f}ms (final), {speedup_ratio:.2f}× speed, type={compression_type})")
    
    subprocess.run(cmd, check=True, capture_output=True)
    Path(temp_trimmed).unlink(missing_ok=True)
    
    if stats is not None:
        stats["speedup"] = speedup_ratio
        stats["compression_type"] = compression_type


def _concatenate_with_gaps(segment_files: List[tuple], output_path: str):
    """
    Concatenate segments with gap silence inserted between them.

    v1 整改：在 concat 前插入 gap 静音段（seg.start - prev.end）。

    Args:
        segment_files: List of (file_path, start_time, end_time) tuples, sorted by start_time
        output_path: Output audio file path
    """
    if not segment_files:
        raise ValueError("No segments to concatenate")

    concat_list = []
    prev_end = 0.0

    for file_path, seg_start, seg_end in segment_files:
        # Insert gap silence if needed (including first segment's leading silence)
        gap = seg_start - prev_end
        if gap > 0.01:  # Only insert if gap > 10ms
            gap_file = Path(file_path).parent / f"gap_{len(concat_list)}.wav"
            _create_silent_audio(str(gap_file), gap)
            concat_list.append(str(gap_file))

        # Add segment
        concat_list.append(file_path)
        prev_end = seg_end

    # v1 整改：确保总时长正确（在最后补静音到最后一个 segment 的 end）
    # 但这里不需要，因为 concat 会自动处理总时长

    # Create concat file list
    concat_file = Path(output_path).parent / "concat_list.txt"
    with open(concat_file, "w") as f:
        for file in concat_list:
            f.write(f"file '{file}'\n")

    # Concatenate using ffmpeg
    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # Clean up
    concat_file.unlink()
    # Clean up gap files
    for file in concat_list:
        if "gap_" in file:
            Path(file).unlink(missing_ok=True)


def synthesize_tts_per_segment(
    dub_manifest: DubManifest,
    voice_assignment: Dict[str, Any],
    voice_pool_path: Optional[str],
    segments_dir: str,
    temp_dir: str,
    *,
    azure_key: str,
    azure_region: str,
    language: str = "en-US",
    max_workers: int = 4,
) -> TTSReport:
    """
    Per-segment TTS synthesis for Timeline-First Architecture.

    Each utterance in dub_manifest is synthesized to an individual WAV file.
    No concatenation is performed (that's handled by Mix phase).

    Args:
        dub_manifest: DubManifest object (SSOT for dubbing)
        voice_assignment: Speaker -> voice mapping
        voice_pool_path: Path to voice pool JSON
        segments_dir: Output directory for per-segment WAVs
        temp_dir: Temporary directory for intermediate files
        azure_key: Azure Speech Service key
        azure_region: Azure Speech Service region
        language: TTS language
        max_workers: Number of concurrent workers (not used in v1)

    Returns:
        TTSReport with per-segment synthesis results
    """
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        raise ImportError(
            "azure-cognitiveservices-speech is not installed. "
            "Install it with: pip install azure-cognitiveservices-speech"
        )

    from dubora_pipeline.models.voice_pool import VoicePool

    voice_pool = VoicePool(pool_path=voice_pool_path)

    # Initialize Azure Speech
    speech_config = speechsdk.SpeechConfig(
        subscription=azure_key,
        region=azure_region,
    )
    speech_config.speech_synthesis_language = language
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )

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

        # Get voice configuration
        voice_info = voice_assignment["speakers"].get(speaker, {})
        voice_id = voice_info.get("voice", {}).get("voice_id", "en-US-JennyNeural")
        pool_key = voice_info.get("voice", {}).get("pool_key")
        prosody = {}
        if pool_key:
            voice_config = voice_pool.get_voice(pool_key)
            prosody = voice_config.get("prosody", {})

        # Generate cache key
        cache_key = _generate_cache_key(text, voice_id, prosody, language)
        cache_file = cache_dir / f"{cache_key}.wav"

        try:
            # Check cache
            if cache_file.exists():
                shutil.copy2(cache_file, segment_file_raw)
                print(f"  💾 [{utt_id}] Cache hit")
            else:
                # Synthesize
                speech_config.speech_synthesis_voice_name = voice_id
                temp_azure_output = temp_path / f"seg_{utt_id}_azure.mp3"
                audio_config = speechsdk.audio.AudioOutputConfig(filename=str(temp_azure_output))

                synthesizer = speechsdk.SpeechSynthesizer(
                    speech_config=speech_config,
                    audio_config=audio_config,
                )

                ssml = f"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{language}">
    <voice name="{voice_id}">
        <prosody rate="{prosody.get('rate', 1.0)}" pitch="{prosody.get('pitch', 0)}%">
            {text}
        </prosody>
    </voice>
</speak>"""

                result = synthesizer.speak_ssml_async(ssml).get()

                if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    _normalize_audio_format(
                        str(temp_azure_output),
                        str(segment_file_raw),
                        sample_rate=CACHE_SAMPLE_RATE,
                        channels=CACHE_CHANNELS,
                    )
                    _write_cache_atomic(cache_file, segment_file_raw)
                    temp_azure_output.unlink(missing_ok=True)
                else:
                    raise RuntimeError(f"TTS failed: {result.reason}")

            # Get raw duration
            raw_ms = _get_duration_ms(str(segment_file_raw))

            # Trim silence
            trimmed_file = temp_path / f"seg_{utt_id}_trimmed.wav"
            trimmed_sec, saved_ms = _trim_silence(str(segment_file_raw), str(trimmed_file))
            trimmed_ms = int(trimmed_sec * 1000)

            # Determine rate and status
            if trimmed_ms <= budget_ms:
                # Fits within budget - pad to exact budget
                _pad_audio(str(trimmed_file), str(segment_file), budget_ms)
                final_ms = budget_ms
                rate = 1.0
                status = TTSSegmentStatus.SUCCESS
            else:
                # Need rate adjustment
                rate = trimmed_ms / budget_ms
                if rate <= max_rate:
                    # Safe rate adjustment
                    _apply_rate_and_pad(str(trimmed_file), str(segment_file), rate, budget_ms)
                    final_ms = budget_ms
                    status = TTSSegmentStatus.RATE_ADJUSTED
                elif allow_extend_ms > 0:
                    # Try with extension
                    extended_budget = budget_ms + allow_extend_ms
                    rate = trimmed_ms / extended_budget
                    if rate <= max_rate:
                        _apply_rate_and_pad(str(trimmed_file), str(segment_file), rate, extended_budget)
                        final_ms = extended_budget
                        status = TTSSegmentStatus.EXTENDED
                    else:
                        # Still too fast - fail fast
                        raise RuntimeError(
                            f"Cannot fit: {trimmed_ms}ms > {extended_budget}ms even at {max_rate}x rate"
                        )
                else:
                    # Fail fast - cannot fit
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
            # Record failure
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
    # Build atempo filter chain (supports 0.5-2.0 range)
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

    # Apply rate, then pad to exact duration
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
