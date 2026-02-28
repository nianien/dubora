"""
混音：TTS + accompaniment/ducking

Functions:
- mix_audio: Original function (uses pre-concatenated TTS audio)
- mix_timeline: New function (Timeline-First Architecture using adelay)
"""
import subprocess
from pathlib import Path
from typing import Optional, List

from dubora.schema.dub_manifest import DubManifest
from dubora.schema.tts_report import TTSReport, TTSSegmentStatus
from dubora.utils.logger import info


def mix_audio(
    tts_path: str,
    accompaniment_path: Optional[str],
    vocals_path: Optional[str],
    video_path: str,
    output_path: str,
    *,
    mute_original: bool = False,
    mix_mode: str = "ducking",  # ducking | simple
    tts_volume: float = 1.0,
    accompaniment_volume: float = 0.8,
    vocals_volume: float = 0.15,
    duck_threshold: float = 0.05,
    duck_ratio: float = 10.0,
    duck_attack_ms: float = 20.0,
    duck_release_ms: float = 400.0,
    target_lufs: float = -16.0,
    true_peak: float = -1.0,
) -> None:
    """
    混音：将 TTS 音频和伴奏混合，输出到视频。
    
    Args:
        tts_path: TTS 音频文件路径
        accompaniment_path: 伴奏音频文件路径（可选）
        vocals_path: 原声人声（sep.vocals，优先用于压制；可选）
        video_path: 原始视频文件路径
        output_path: 输出视频文件路径
        mix_mode: 混音模式：ducking（侧链压制原声，推荐）或 simple（固定压低原声）
        tts_volume: TTS 音量增益（线性）
        accompaniment_volume: 伴奏音量增益（线性）
        vocals_volume: 原声人声音量增益（线性，ducking 前的基准）
        duck_threshold: 侧链阈值（0~1 振幅，越小越激进）
        duck_ratio: 压缩比（越大压制越明显）
        duck_attack_ms: attack（毫秒）
        duck_release_ms: release（毫秒）
        target_lufs: 目标响度（LUFS）
        true_peak: 真峰值限制（dB）
    """
    tts_file = Path(tts_path)
    video_file = Path(video_path)
    output_file = Path(output_path)
    accomp_file = Path(accompaniment_path) if accompaniment_path else None
    vocals_file = Path(vocals_path) if vocals_path else None
    
    if not tts_file.exists():
        raise FileNotFoundError(f"TTS audio not found: {tts_path}")
    if not video_file.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Inputs:
    # 0: video (for video stream + optional audio fallback)
    # 1: tts
    # 2: accompaniment (optional)
    # 3: vocals (optional)
    inputs = [str(video_file), str(tts_file)]
    if accomp_file and accomp_file.exists():
        inputs.append(str(accomp_file))
    if vocals_file and vocals_file.exists():
        inputs.append(str(vocals_file))

    has_accomp = accomp_file is not None and accomp_file.exists()
    has_vocals = vocals_file is not None and vocals_file.exists()

    # Labels
    # [tts_sc]  sidechain key for ducking（仅在需要 ducking 时存在）
    # [tts_mix] actual TTS audio to be mixed
    # [bg]      from accompaniment if available, else from video audio
    # [orig]    original speech to duck: prefer vocals, else video audio
    # 重要：同一个 labeled stream 不能被消费两次，只在需要 ducking 时 asplit。
    if not mute_original and mix_mode == "ducking":
        tts_chain = f"[1:a]volume={tts_volume},asplit=2[tts_sc][tts_mix]"
    else:
        # 不做 ducking 或完全 mute 原声时，不需要 sidechain 分支
        tts_chain = f"[1:a]volume={tts_volume}[tts_mix]"

    if has_accomp:
        bg_chain = f"[2:a]volume={accompaniment_volume}[bg]"
    else:
        # 没有伴奏时，只能用视频原音做 bg（可能包含对白）
        bg_chain = "[0:a]anull[bg]"

    filter_parts = [tts_chain, bg_chain]

    if not mute_original:
        # 构建原声链
        if has_vocals:
            # vocals input index depends on whether accompaniment exists
            vocals_input_idx = 3 if has_accomp else 2
            orig_chain = f"[{vocals_input_idx}:a]volume={vocals_volume}[orig]"
        else:
            # 没有 sep.vocals，只能用视频原声做 orig
            orig_chain = f"[0:a]volume={vocals_volume}[orig]"

        filter_parts.append(orig_chain)

        # Ducking / mixing
        if mix_mode == "ducking":
            # Sidechain duck original speech under TTS（以 tts_sc 为侧链键控）。
            duck = (
                f"[orig][tts_sc]"
                f"sidechaincompress="
                f"threshold={duck_threshold}:"
                f"ratio={duck_ratio}:"
                f"attack={duck_attack_ms}:"
                f"release={duck_release_ms}:"
                f"detection=peak:"
                f"link=maximum"
                f"[orig_duck]"
            )
        else:
            # Simple fallback: keep original speech always low (no sidechain)
            duck = "[orig]anull[orig_duck]"

        # Mix: bg + orig_duck + tts_mix
        # If bg is from video audio and orig is also from video audio, this will double-count.
        # 我们接受这种退化情况（未分离出 vocals/accompaniment 时）。
        mix = "[bg][orig_duck][tts_mix]amix=inputs=3:duration=longest:weights=1 1 3[mix]"
        filter_parts.extend([duck, mix])
    else:
        # 完全不要中文人声：只保留 bg（优先伴奏）+ TTS
        # 如果有伴奏，bg 基本是纯 BGM；没有伴奏时，bg 仍然可能带对白，这是无法避免的退化情况。
        mix = "[bg][tts_mix]amix=inputs=2:duration=longest:weights=1 3[mix]"
        filter_parts.append(mix)

    # Loudness normalization (one-pass)
    norm = f"[mix]loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:linear=true[final]"
    filter_parts.append(norm)

    filter_complex = ";".join(filter_parts)
    
    cmd = [
        "ffmpeg",
        "-i", inputs[0],
        "-i", inputs[1],
    ]
    
    if len(inputs) > 2:
        cmd.extend(["-i", inputs[2]])
    if len(inputs) > 3:
        cmd.extend(["-i", inputs[3]])
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[final]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-y",
        str(output_file),
    ])
    
    info(
        "Mixing audio:"
        f" mute_original={mute_original}, mode={mix_mode}, has_accomp={has_accomp}, has_vocals={has_vocals},"
        f" duck(thr={duck_threshold}, ratio={duck_ratio}, attack={duck_attack_ms}, release={duck_release_ms})"
    )
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        info(f"Mix completed: {output_file.name}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"FFmpeg mix failed: {e.stderr or e.stdout or 'Unknown error'}"
        ) from e


def mix_timeline(
    dub_manifest: DubManifest,
    tts_report: TTSReport,
    segments_dir: str,
    video_path: str,
    *,
    accompaniment_path: Optional[str] = None,
    vocals_path: Optional[str] = None,
    mute_original: bool = True,
    mix_mode: str = "ducking",
    tts_volume: float = 1.0,
    accompaniment_volume: float = 0.8,
    vocals_volume: float = 0.15,
    duck_threshold: float = 0.05,
    duck_ratio: float = 10.0,
    duck_attack_ms: float = 20.0,
    duck_release_ms: float = 400.0,
    target_lufs: float = -16.0,
    true_peak: float = -1.0,
    output_path: str,
    singing_segments: Optional[List[tuple]] = None,
) -> int:
    """
    Timeline-based mixing using adelay for segment placement.

    Uses dub_manifest for timing information and places each TTS segment
    at its correct start_ms position using FFmpeg's adelay filter.
    Forces exact duration using apad + atrim.

    Args:
        dub_manifest: DubManifest object (SSOT for timing)
        tts_report: TTSReport object (segment info)
        segments_dir: Directory containing per-segment WAV files
        video_path: Original video file path
        accompaniment_path: Accompaniment audio path (optional)
        vocals_path: Original vocals path (optional)
        mute_original: Mute original dialogue (default True)
        mix_mode: Mix mode (ducking or simple)
        tts_volume: TTS volume multiplier
        accompaniment_volume: Accompaniment volume multiplier
        vocals_volume: Vocals volume multiplier
        duck_*: Ducking parameters
        target_lufs: Target loudness
        true_peak: True peak limit
        output_path: Output audio file path

    Returns:
        Actual duration in milliseconds
    """
    video_file = Path(video_path)
    output_file = Path(output_path)
    segments_path = Path(segments_dir)
    accomp_file = Path(accompaniment_path) if accompaniment_path else None
    vocals_file = Path(vocals_path) if vocals_path else None

    if not video_file.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Target duration from manifest
    target_duration_ms = dub_manifest.audio_duration_ms
    target_duration_sec = target_duration_ms / 1000.0

    # Build list of valid segments with their timing
    segment_info: List[tuple] = []  # (seg_path, start_ms, utt_id)

    for utt in dub_manifest.utterances:
        utt_id = utt.utt_id
        start_ms = utt.start_ms

        # Find corresponding segment file
        seg_path = segments_path / f"seg_{utt_id}.wav"
        if not seg_path.exists():
            info(f"Segment not found: {seg_path}, skipping")
            continue

        # Check if segment was successfully synthesized
        seg_report = next(
            (s for s in tts_report.segments if s.utt_id == utt_id),
            None
        )
        if seg_report and seg_report.status == TTSSegmentStatus.FAILED:
            info(f"Segment {utt_id} failed synthesis, skipping")
            continue

        segment_info.append((str(seg_path), start_ms, utt_id))

    if not segment_info:
        raise ValueError("No valid segments to mix")

    info(f"Timeline mixing: {len(segment_info)} segments, target duration: {target_duration_sec:.2f}s")

    # Build FFmpeg command
    # Input structure:
    # 0: video (for optional audio fallback)
    # 1..N: segment files
    # N+1: accompaniment (if exists)
    # N+2: vocals (if exists)

    inputs = [str(video_file)]
    for seg_path, _, _ in segment_info:
        inputs.append(seg_path)

    accomp_input_idx = None
    vocals_input_idx = None

    has_accomp = accomp_file is not None and accomp_file.exists()
    has_vocals = vocals_file is not None and vocals_file.exists()

    if has_accomp:
        accomp_input_idx = len(inputs)
        inputs.append(str(accomp_file))

    if has_vocals:
        vocals_input_idx = len(inputs)
        inputs.append(str(vocals_file))

    # Build filter graph
    filter_parts = []
    segment_labels = []

    # Process each segment: apply adelay to place at correct position
    for i, (seg_path, start_ms, utt_id) in enumerate(segment_info):
        input_idx = i + 1  # Segment inputs start at index 1
        label = f"s{i}"

        # adelay: delay both channels (L|R format) by start_ms milliseconds
        # Volume adjustment applied
        filter_parts.append(
            f"[{input_idx}:a]volume={tts_volume},adelay={start_ms}|{start_ms}[{label}]"
        )
        segment_labels.append(f"[{label}]")

    # Mix all TTS segments together
    if len(segment_labels) > 1:
        tts_mix_inputs = "".join(segment_labels)
        filter_parts.append(
            f"{tts_mix_inputs}amix=inputs={len(segment_labels)}:duration=longest:normalize=0[tts_raw]"
        )
    else:
        # Single segment - just rename
        filter_parts.append(f"{segment_labels[0]}anull[tts_raw]")

    # Singing segments: extract original vocal windows
    singing_segments = singing_segments or []
    singing_vocal_label = None
    if singing_segments and has_vocals:
        singing_labels = []
        for si, (s_start, s_end) in enumerate(singing_segments):
            s_start_sec = s_start / 1000.0
            s_dur_sec = (s_end - s_start) / 1000.0
            label = f"sing{si}"
            # Extract time window from vocals, then place at correct position with adelay
            filter_parts.append(
                f"[{vocals_input_idx}:a]atrim=start={s_start_sec}:duration={s_dur_sec},"
                f"asetpts=PTS-STARTPTS,adelay={s_start}|{s_start}[{label}]"
            )
            singing_labels.append(f"[{label}]")

        if len(singing_labels) > 1:
            filter_parts.append(
                f"{''.join(singing_labels)}amix=inputs={len(singing_labels)}:duration=longest:normalize=0[sing_mix]"
            )
        else:
            filter_parts.append(f"{singing_labels[0]}anull[sing_mix]")
        singing_vocal_label = "[sing_mix]"
        info(f"Singing segments: {len(singing_segments)} windows, keeping original vocals")

    # Prepare sidechain if needed for ducking
    if not mute_original and mix_mode == "ducking":
        filter_parts.append("[tts_raw]asplit=2[tts_sc][tts_mix]")
    else:
        filter_parts.append("[tts_raw]anull[tts_mix]")

    # Background audio
    if has_accomp:
        filter_parts.append(f"[{accomp_input_idx}:a]volume={accompaniment_volume}[bg]")
    else:
        filter_parts.append("[0:a]anull[bg]")

    # Original vocals for ducking
    if not mute_original:
        if has_vocals:
            filter_parts.append(f"[{vocals_input_idx}:a]volume={vocals_volume}[orig]")
        else:
            filter_parts.append(f"[0:a]volume={vocals_volume}[orig]")

        if mix_mode == "ducking":
            filter_parts.append(
                f"[orig][tts_sc]sidechaincompress="
                f"threshold={duck_threshold}:"
                f"ratio={duck_ratio}:"
                f"attack={duck_attack_ms}:"
                f"release={duck_release_ms}:"
                f"detection=peak:link=maximum[orig_duck]"
            )
        else:
            filter_parts.append("[orig]anull[orig_duck]")

        if singing_vocal_label:
            filter_parts.append(
                f"[bg][orig_duck][tts_mix]{singing_vocal_label}amix=inputs=4:duration=longest:weights=1 1 3 3:normalize=0[mix_raw]"
            )
        else:
            filter_parts.append(
                "[bg][orig_duck][tts_mix]amix=inputs=3:duration=longest:weights=1 1 3:normalize=0[mix_raw]"
            )
    else:
        # Mute original: only BGM + TTS (+ singing vocals if any)
        if singing_vocal_label:
            filter_parts.append(
                f"[bg][tts_mix]{singing_vocal_label}amix=inputs=3:duration=longest:weights=1 3 3:normalize=0[mix_raw]"
            )
        else:
            filter_parts.append(
                "[bg][tts_mix]amix=inputs=2:duration=longest:weights=1 3:normalize=0[mix_raw]"
            )

    # Force exact duration with apad + atrim
    # apad: pad with silence to reach target duration
    # atrim: trim to exact target duration
    filter_parts.append(
        f"[mix_raw]apad=whole_dur={target_duration_sec},"
        f"atrim=duration={target_duration_sec}[mix_dur]"
    )

    # Loudness normalization
    filter_parts.append(
        f"[mix_dur]loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:linear=true[final]"
    )

    filter_complex = ";".join(filter_parts)

    # Build ffmpeg command
    cmd = ["ffmpeg"]
    for input_file in inputs:
        cmd.extend(["-i", input_file])

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[final]",
        "-acodec", "pcm_s16le",
        "-ar", "24000",
        "-ac", "1",
        "-y",
        str(output_file),
    ])

    info(
        f"Timeline mixing: {len(segment_info)} segments, "
        f"mute_original={mute_original}, mode={mix_mode}, "
        f"has_accomp={has_accomp}, has_vocals={has_vocals}"
    )

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        info(f"Mix completed: {output_file.name}")
    except subprocess.CalledProcessError as e:
        # Print stderr for debugging
        error_msg = e.stderr or e.stdout or "Unknown error"
        info(f"FFmpeg stderr: {error_msg[:1000]}")
        raise RuntimeError(f"FFmpeg mix failed: {error_msg}") from e

    # Verify output duration
    actual_duration_ms = _probe_duration_ms(str(output_file))
    info(f"Output duration: {actual_duration_ms}ms (target: {target_duration_ms}ms)")

    return actual_duration_ms


def _probe_duration_ms(audio_path: str) -> int:
    """Probe audio duration using ffprobe."""
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
