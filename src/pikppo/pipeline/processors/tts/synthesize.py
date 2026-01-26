"""
文本转语音（TTS）合成模块。
将英文字幕转换为语音，并根据时间戳合成音频。
"""
import hashlib
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

from pikppo.config.settings import PipelineConfig, get_openai_key
from pikppo.pipeline.processors.subtitle import parse_srt
from pikppo.schema import SrtCue


def synthesize_subtitle_to_audio(
    subtitle_path: str,
    *,
    output_path: Optional[str] = None,
    video_path: Optional[str] = None,
    config: Optional[PipelineConfig] = None,
    voice: str = "alloy",
    speed: float = 1.0,
    replace_audio: bool = False,
) -> Dict[str, str]:
    """
    将英文字幕文件转换为语音，并合成音频。
    
    参数:
        subtitle_path: 英文字幕文件路径（SRT）
        output_path: 输出音频文件路径（默认：{subtitle}_audio.wav）
        video_path: 原始视频文件路径（如果提供，可以替换或混合音频）
        config: PipelineConfig
        voice: TTS 语音（OpenAI TTS: alloy, echo, fable, onyx, nova, shimmer）
        speed: 语音速度（0.25-4.0，默认 1.0）
        replace_audio: 如果提供 video_path，是否替换原音频（True）或混合（False）
    
    返回:
        {"audio": "<音频文件路径>", "video": "<视频文件路径>"（如果提供）}
    """
    cfg = config or PipelineConfig()
    
    subtitle = Path(subtitle_path)
    if not subtitle.exists():
        raise FileNotFoundError(f"Subtitle file not found: {subtitle}")
    
    # 解析字幕
    cues = parse_srt(subtitle)  # 返回 List[SrtCue]
    if not cues:
        raise RuntimeError(f"No segments found in subtitle file: {subtitle}")
    
    # 输出路径：确保与输入文件在同一目录
    if output_path is None:
        # 默认输出到输入文件同一目录
        output_audio = subtitle.parent / f"{subtitle.stem}_audio.wav"
    else:
        output_audio = Path(output_path)
        # 如果输出路径是相对路径，则相对于输入文件目录
        if not output_audio.is_absolute():
            output_audio = subtitle.parent / output_audio
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print("字幕转语音（TTS）")
    print("=" * 60)
    print(f"字幕文件: {subtitle}")
    print(f"输出音频: {output_audio}")
    print(f"语音: {voice}, 速度: {speed}x")
    print(f"字幕片段数: {len(cues)}")
    print("=" * 60)
    print()
    
    # 检查 OpenAI API Key
    openai_key = get_openai_key()
    if not openai_key:
        raise RuntimeError(
            "OPENAI_KEY or OPENAI_API_KEY not found in environment variables. "
            "Please set it to use TTS."
        )
    
    # 使用 OpenAI TTS 合成语音（并发处理）
    print("1. 合成语音片段（并发处理）...")
    audio_segments = []
    
    # 准备缓存目录（用于 TTS 片段缓存）
    cache_dir = subtitle.parent / ".cache" / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # 准备需要合成的任务
        tasks = []
        for i, cue in enumerate(cues):
            # SrtCue 对象有 text 属性
            text = cue.text.strip()
            if not text:
                continue
            
            # SrtCue 使用毫秒，转换为秒
            start_time = cue.start_ms / 1000.0
            end_time = cue.end_ms / 1000.0
            duration = end_time - start_time
            
            # 检查 TTS 缓存
            cache_key = hashlib.md5(f"{text}_{voice}_{speed}".encode()).hexdigest()
            cached_segment = cache_dir / f"{cache_key}.mp3"
            
            if cached_segment.exists():
                # 使用缓存
                audio_segments.append({
                    "path": cached_segment,
                    "start": start_time,
                    "duration": duration,
                })
                print(f"   💾 [{i+1}/{len(segments)}] 缓存命中: {text[:50]}...")
            else:
                # 需要合成
                segment_audio = tmpdir_path / f"segment_{i:04d}.mp3"
                tasks.append({
                    "index": i,
                    "total": len(segments),
                    "text": text,
                    "output_path": str(segment_audio),
                    "cache_path": cached_segment,
                    "start": start_time,
                    "duration": duration,
                })
        
        # 并发合成（默认 4 个 worker，可根据网络/配额调整）
        max_workers = getattr(cfg, "tts_max_workers", 4)
        if tasks:
            print(f"   🚀 并发合成 {len(tasks)} 个片段（workers={max_workers}）...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _synthesize_text_openai,
                        text=task["text"],
                        output_path=task["output_path"],
                        voice=voice,
                        speed=speed,
                        api_key=openai_key,
                    ): task
                    for task in tasks
                }
                
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        future.result()  # 等待完成
                        # 复制到缓存
                        import shutil
                        shutil.copy(task["output_path"], task["cache_path"])
                        
                        audio_segments.append({
                            "path": Path(task["output_path"]),
                            "start": task["start"],
                            "duration": task["duration"],
                        })
                        print(f"   ✅ [{task['index']+1}/{task['total']}] {task['text'][:50]}...")
                    except Exception as e:
                        print(f"   ⚠️  [{task['index']+1}/{task['total']}] 失败: {e}")
                        continue
        
        if not audio_segments:
            raise RuntimeError("No audio segments generated. Check TTS API key and network.")
        
        print()
        print(f"2. 合成完整音频（共 {len(audio_segments)} 个片段）...")
        
        # 先合成原始音频（未归一化）
        raw_audio = tmpdir_path / "audio_raw.wav"
        _concatenate_audio_segments(
            audio_segments=audio_segments,
            output_path=str(raw_audio),
        )
        print(f"   ✅ 原始音频已合成")
        
        # 3. 响度归一化（关键步骤：解决声音小的问题）
        print()
        print("3. 响度归一化（Loudness Normalization）...")
        target_lufs = getattr(cfg, "tts_target_lufs", -16.0)  # 默认 -16 LUFS（TikTok/短剧推荐）
        normalize_tts_audio(
            input_path=str(raw_audio),
            output_path=str(output_audio),
            target_lufs=target_lufs,
        )
        print(f"   ✅ 响度归一化完成（目标: {target_lufs} LUFS）")
    
    result: Dict[str, str] = {"audio": str(output_audio)}
    
    # 如果提供了视频路径，替换或混合音频
    # 输出视频与输入字幕在同一目录（而不是视频目录）
    if video_path:
        video = Path(video_path)
        if not video.exists():
            raise FileNotFoundError(f"Video file not found: {video}")
        
        print()
        print("4. 替换/混合视频音频...")
        # 输出视频与字幕文件在同一目录
        output_video = subtitle.parent / f"{subtitle.stem}_dubbed.mp4"
        
        if replace_audio or getattr(cfg, "tts_mute_original", False):
            # 完全替换音频（静音原音频）
            if getattr(cfg, "tts_mute_original", False):
                print("   🔇 完全静音原音频模式：只保留英文配音")
            else:
                print("   ⚠️  完全替换音频模式：将丢失原视频 BGM/环境声")
            _replace_video_audio(str(video), str(output_audio), str(output_video))
        else:
            # 专业混音（推荐）：使用侧链压缩，保留原音氛围
            mix_mode = getattr(cfg, "tts_mix_mode", "ducking")
            scene_type = getattr(cfg, "tts_scene_type", "normal")
            
            if mix_mode == "ducking":
                # 使用专业的侧链压缩混音
                mix_video_audio_with_ducking(
                    str(video),
                    str(output_audio),
                    str(output_video),
                    scene_type=scene_type,
                    tts_volume=getattr(cfg, "tts_volume", 1.4),
                )
            else:
                # 简单混合（保守方案）
                _mix_video_audio(
                    str(video),
                    str(output_audio),
                    str(output_video),
                    mix_mode="simple",
                    original_volume=0.4,
                    tts_volume=getattr(cfg, "tts_volume", 1.4),
                )
        
        print(f"   ✅ 视频已生成: {output_video}")
        result["video"] = str(output_video)
    
    print()
    print("=" * 60)
    print("✅ 完成！")
    print("=" * 60)
    
    return result


def _synthesize_text_openai(
    text: str,
    output_path: str,
    voice: str = "alloy",
    speed: float = 1.0,
    api_key: Optional[str] = None,
) -> None:
    """使用 OpenAI TTS API 合成语音。"""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "openai package is required for TTS. Install it with: pip install openai"
        )
    
    client = OpenAI(api_key=api_key)
    
    response = client.audio.speech.create(
        model="tts-1",  # 或 "tts-1-hd" (更高质量，更慢)
        voice=voice,
        input=text,
        speed=speed,
    )
    
    # 保存音频
    response.stream_to_file(output_path)


def _concatenate_audio_segments(
    audio_segments: List[Dict],
    output_path: str,
) -> None:
    """
    将多个音频片段按时间戳合成完整音频。
    
    参数:
        audio_segments: 列表，每个元素包含 {"path": Path, "start": float, "duration": float}
        output_path: 输出音频文件路径
    """
    if not audio_segments:
        raise ValueError("No audio segments to concatenate")
    
    # 计算总时长
    max_end = max(seg["start"] + seg["duration"] for seg in audio_segments)
    total_duration = max_end
    
    # 生成 ffmpeg filter_complex
    # 为每个片段创建延迟和填充
    delays = []
    mix_inputs = []
    
    for i, seg in enumerate(audio_segments):
        delay_ms = int(seg["start"] * 1000)
        # 为每个输入添加延迟
        delays.append(f"[{i}]adelay={delay_ms}|{delay_ms}[a{i}]")
        mix_inputs.append(f"[a{i}]")
    
    # 构建 filter_complex
    if len(audio_segments) == 1:
        filter_complex = f"{delays[0]};[a0]volume=1.0[out]"
    else:
        # 合并所有延迟后的音频流
        filter_complex = ";".join(delays) + f";{''.join(mix_inputs)}amix=inputs={len(audio_segments)}:duration=longest:dropout_transition=0[out]"
    
    # 构建 ffmpeg 命令
    cmd = ["ffmpeg", "-y"]
    
    # 添加所有输入文件
    for seg in audio_segments:
        cmd.extend(["-i", str(seg["path"])])
    
    # 添加 filter_complex
    cmd.extend(["-filter_complex", filter_complex])
    
    # 输出设置
    cmd.extend([
        "-map", "[out]",
        "-ar", "44100",
        "-ac", "2",
        "-acodec", "pcm_s16le",
        output_path,
    ])
    
    subprocess.run(cmd, check=True, capture_output=True)


def _replace_video_audio(video_path: str, audio_path: str, output_path: str) -> None:
    """替换视频的音频轨道。"""
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True)


def normalize_tts_audio(
    input_path: str,
    output_path: str,
    target_lufs: float = -16.0,
) -> None:
    """
    对 TTS 音频进行响度归一化（Loudness Normalization）。
    
    这是解决 TTS 声音小问题的关键步骤。
    TTS 服务默认输出保守电平（通常 -22 ~ -24 LUFS），
    需要归一化到平台标准（-16 LUFS 适合 TikTok/短剧）。
    
    参数:
        input_path: 输入音频文件（原始 TTS 输出）
        output_path: 输出音频文件（归一化后）
        target_lufs: 目标响度（LUFS）
            - -16 LUFS: TikTok/短剧推荐
            - -14 LUFS: YouTube/Shorts
            - -23 LUFS: 广播标准
    
    注意:
        - loudnorm 必须在最后做（对整条音轨）
        - 不要在每句单独做，会破坏一致性
    """
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-c:a", "pcm_s16le",
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _mix_video_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    mix_mode: str = "ducking",
    original_volume: float = 1.0,
    tts_volume: float = 1.4,
) -> None:
    """
    混合视频原音频和新的 TTS 音频（专业混音）。
    
    支持两种混音模式：
    1. ducking（推荐）：侧链压缩，当 TTS 响起时自动压低原音频
    2. simple：简单混合，固定压低原音频
    
    参数:
        video_path: 原始视频文件
        audio_path: TTS 音频文件（已归一化）
        output_path: 输出视频文件
        mix_mode: 混音模式（"ducking" 或 "simple"）
        original_volume: 原音频基础音量（ducking 模式下为峰值，simple 模式下为固定值）
        tts_volume: TTS 音频音量（默认 1.4，稍微放大确保清晰）
    """
    if mix_mode == "ducking":
        # 侧链压缩（Sidechain Ducking）- 使用 acompressor 实现
        # 当英文配音出现时，自动压低原视频音量；配音结束后，自动恢复
        # 使用 acompressor 的侧链功能：用 TTS 音频作为侧链信号控制原音频
        filter_complex = (
            f"[0:a]acompressor=threshold=0.02:ratio=10:attack=20:release=500:makeup=1.0:detection=peak:link=1[a0_compressed];"
            f"[1:a]volume={tts_volume}[a1];"
            f"[a0_compressed][a1]amix=inputs=2:weights=1 3:duration=longest[mix]"
        )
    else:
        # 简单混合（保守方案）
        # 全程大幅压低原音频，确保英文配音清晰
        filter_complex = (
            f"[0:a]volume={original_volume * 0.2}[a0];"  # 原音频压低到 20%（更激进）
            f"[1:a]volume={tts_volume}[a1];"
            f"[a0][a1]amix=inputs=2:weights=1 5:duration=longest[mix]"  # TTS 权重 5:1
        )
    
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[mix]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True)


def mix_video_audio_with_ducking(
    video_path: str,
    audio_path: str,
    output_path: str,
    *,
    scene_type: str = "normal",
    tts_volume: float = 1.4,
) -> None:
    """
    专业的视频音频混音（带侧链压缩）。
    
    这是短剧出海的标准做法：
    - 保留原视频 BGM / 环境声
    - 当英文配音出现时，自动压低原视频音量（ducking）
    - 配音结束后，自动恢复原音量
    
    参数:
        video_path: 原始视频文件
        audio_path: TTS 音频文件（已归一化）
        output_path: 输出视频文件
        scene_type: 场景类型，影响混音参数
            - "quiet": 安静场景（更激进的 ducking）
            - "normal": 正常场景（默认）
            - "intense": 激烈场景（保留更多原音氛围）
        tts_volume: TTS 音频音量（默认 1.4）
    
    混音策略：
    - quiet: 更低的 threshold，更快的 attack，确保配音清晰
    - normal: 平衡的 ducking 参数
    - intense: 更高的 threshold，保留更多原音氛围
    """
    # 根据场景类型调整 ducking 参数
    ducking_params = {
        "quiet": {
            "threshold": 0.015,  # 更敏感
            "ratio": 12,  # 更强的压缩
            "attack": 15,  # 更快的响应
            "release": 400,  # 更快的恢复
        },
        "normal": {
            "threshold": 0.02,
            "ratio": 10,
            "attack": 20,
            "release": 500,
        },
        "intense": {
            "threshold": 0.03,  # 更不敏感，保留更多原音
            "ratio": 8,  # 更温和的压缩
            "attack": 30,  # 稍慢的响应
            "release": 600,  # 更慢的恢复
        },
    }
    
    params = ducking_params.get(scene_type, ducking_params["normal"])
    
    # 使用侧链压缩（sidechaincompress）：用 TTS 音频作为侧链信号控制原音频
    # 当 TTS 响起时，自动压低原音频；TTS 结束后，自动恢复
    # makeup 参数范围是 1-64，1 表示不增益，更大的值表示增益
    # 我们使用 1.0（最小增益）来压低原音频，然后通过 volume 进一步控制
    filter_complex = (
        f"[0:a][1:a]sidechaincompress="
        f"threshold={params['threshold']}:"
        f"ratio={params['ratio']}:"
        f"attack={params['attack']}:"
        f"release={params['release']}:"
        f"makeup=1.0[a0_compressed];"  # makeup=1.0（最小增益，配合 volume 压低）
        f"[a0_compressed]volume=0.2[a0_low];"  # 进一步压低原音频到 20%
        f"[1:a]volume={tts_volume}[a1];"
        f"[a0_low][a1]amix=inputs=2:weights=1 5:duration=longest[mix]"  # TTS 权重 5:1
    )
    
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[mix]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-y",
        output_path,
    ]
    subprocess.run(cmd, check=True)
