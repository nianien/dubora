"""
混音：TTS + accompaniment/ducking
"""
import subprocess
from pathlib import Path
from typing import Optional

from pikppo.utils.logger import info


def mix_audio(
    tts_path: str,
    accompaniment_path: Optional[str],
    video_path: str,
    output_path: str,
    *,
    target_lufs: float = -16.0,
    true_peak: float = -1.0,
) -> None:
    """
    混音：将 TTS 音频和伴奏混合，输出到视频。
    
    Args:
        tts_path: TTS 音频文件路径
        accompaniment_path: 伴奏音频文件路径（可选）
        video_path: 原始视频文件路径
        output_path: 输出视频文件路径
        target_lufs: 目标响度（LUFS）
        true_peak: 真峰值限制（dB）
    """
    tts_file = Path(tts_path)
    video_file = Path(video_path)
    output_file = Path(output_path)
    
    if not tts_file.exists():
        raise FileNotFoundError(f"TTS audio not found: {tts_path}")
    if not video_file.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 构建混音命令
    # 如果有伴奏，混合 TTS + 伴奏；否则只使用 TTS
    if accompaniment_path and Path(accompaniment_path).exists():
        # 混合 TTS + 伴奏
        filter_complex = (
            f"[0:a]volume=0.3[bg];"  # 原视频音频压低
            f"[1:a]volume=1.0[tts];"  # TTS
            f"[2:a]volume=0.5[accomp];"  # 伴奏
            f"[tts][accomp]amix=inputs=2:duration=longest[mixed];"
            f"[bg][mixed]amix=inputs=2:weights=1 3:duration=longest[final]"
        )
        inputs = [str(video_file), str(tts_file), str(accompaniment_path)]
    else:
        # 只使用 TTS，压低原视频音频
        filter_complex = (
            f"[0:a]volume=0.2[bg];"  # 原视频音频压低到 20%
            f"[1:a]volume=1.0[tts];"  # TTS
            f"[bg][tts]amix=inputs=2:weights=1 5:duration=longest[final]"  # TTS 权重更高
        )
        inputs = [str(video_file), str(tts_file)]
    
    cmd = [
        "ffmpeg",
        "-i", inputs[0],
        "-i", inputs[1],
    ]
    
    if len(inputs) > 2:
        cmd.extend(["-i", inputs[2]])
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[final]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-y",
        str(output_file),
    ])
    
    info(f"Mixing audio: TTS + {'accompaniment' if accompaniment_path else 'video audio'}...")
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
