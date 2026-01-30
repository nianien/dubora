"""
音频提取工具：从视频文件中提取音频

职责：
- 提取 16kHz 单声道 WAV 音频（用于 ASR）
- 使用 ffmpeg 进行音频提取
"""
import subprocess
from pathlib import Path
from pikppo.utils.logger import info, error


def extract_raw_audio(video_path: str, output_path: str) -> None:
    """
    从视频文件中提取 16kHz 单声道 WAV 音频。
    
    Args:
        video_path: 输入视频文件路径
        output_path: 输出音频文件路径（.wav）
    
    Raises:
        FileNotFoundError: 如果视频文件不存在
        RuntimeError: 如果 ffmpeg 提取失败
    """
    video_file = Path(video_path)
    if not video_file.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 使用 ffmpeg 提取音频：16kHz, 单声道, PCM 16-bit
    cmd = [
        "ffmpeg",
        "-i", str(video_file),
        "-ar", "16000",      # 采样率 16kHz
        "-ac", "1",          # 单声道
        "-acodec", "pcm_s16le",  # PCM 16-bit little-endian
        "-y",                 # 覆盖输出文件
        str(output_file),
    ]
    
    # 验证视频文件大小
    video_size = video_file.stat().st_size
    if video_size == 0:
        raise RuntimeError(
            f"Video file is empty (0 bytes): {video_path}. "
            f"Please check if the video file is corrupted or incomplete."
        )
    
    info(f"Extracting audio from {video_file.name} (size: {video_size / 1024 / 1024:.2f} MB)...")
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        
        # 验证输出文件是否存在且非空
        if not output_file.exists():
            raise RuntimeError(
                f"Audio extraction failed: output file was not created: {output_path}"
            )
        
        output_size = output_file.stat().st_size
        if output_size == 0:
            # 尝试从 stderr 获取更多信息
            error_msg = result.stderr if result.stderr else "Unknown error"
            raise RuntimeError(
                f"Audio extraction failed: output file is empty (0 bytes). "
                f"This usually means:\n"
                f"  1. The video file is corrupted or incomplete\n"
                f"  2. The video file has no audio track\n"
                f"  3. FFmpeg encountered an error but didn't report it\n"
                f"FFmpeg output: {error_msg}"
            )
        
        info(f"Audio extracted successfully: {output_file.name} (size: {output_size / 1024 / 1024:.2f} MB)")
    except subprocess.CalledProcessError as e:
        error(f"FFmpeg extraction failed:")
        error(f"  Command: {' '.join(cmd)}")
        error(f"  Return code: {e.returncode}")
        if e.stderr:
            error(f"  Error output: {e.stderr}")
        if e.stdout:
            error(f"  Standard output: {e.stdout}")
        raise RuntimeError(
            f"Failed to extract audio from {video_path}. "
            f"FFmpeg error: {e.stderr or e.stdout or 'Unknown error'}"
        ) from e
    except FileNotFoundError:
        raise RuntimeError(
            "FFmpeg not found. Please install ffmpeg: "
            "brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)"
        )
