"""
Media Processor: 音频提取（唯一对外入口）

职责：
- 接收 Phase 层的输入（video_path）
- 调用内部实现提取音频
- 返回 ProcessorResult（不负责文件 IO）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在 impl.py
- Phase 层只调用 processor.run()
"""
from pathlib import Path

from .._types import ProcessorResult
from .impl import extract_raw_audio


def run(
    video_path: str,
    *,
    output_path: str,
) -> ProcessorResult:
    """
    从视频文件中提取 16kHz 单声道 WAV 音频。
    
    Args:
        video_path: 输入视频文件路径
        output_path: 输出音频文件路径（.wav）
    
    Returns:
        ProcessorResult:
        - data.output_path: 输出音频文件路径
        - meta: 元数据（audio_size_mb 等）
    """
    extract_raw_audio(video_path, output_path)
    
    # 验证输出文件
    output_file = Path(output_path)
    if not output_file.exists():
        raise RuntimeError(f"Audio extraction failed: {output_path} was not created")
    
    output_size = output_file.stat().st_size
    if output_size == 0:
        raise RuntimeError(f"Audio extraction failed: {output_path} is empty")
    
    return ProcessorResult(
        outputs=[],  # 由 Phase 声明 outputs，processor 只负责业务处理
        data={
            "output_path": output_path,
        },
        metrics={
            "audio_size_mb": output_size / 1024 / 1024,
        },
    )
