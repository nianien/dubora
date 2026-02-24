"""
SRT 渲染器：Subtitle Model → SRT 文件

职责：
- 将 Subtitle Model (Segment[]) 渲染为 SRT 文件
- 这是 Subtitle Model 的派生视图，不修改原始模型

架构原则：
- Subtitle Model 是 SSOT（唯一事实源）
- SRT 文件是 Subtitle Model 的视图
- 不反向修改 Subtitle Model 语义
"""
from pathlib import Path
from typing import List

from dubora.schema import Segment
from .srt import segments_to_srt_cues, write_srt


def render_srt(segments: List[Segment], output_path: Path) -> None:
    """
    将 Subtitle Model 渲染为 SRT 文件。
    
    Args:
        segments: Subtitle Model（Segment[]，SSOT）
        output_path: SRT 文件输出路径
    
    注意：
    - segments 是 Subtitle Model，包含完整的语义信息（speaker, emotion, gender）
    - SRT 文件只包含时间轴和文本（不包含 speaker/emotion/gender）
    - 这是单向转换：Model → View，不反向修改 Model
    """
    # Segment[] → SrtCue[]（去掉 speaker/emotion/gender，只保留时间轴和文本）
    cues = segments_to_srt_cues(segments)
    
    # SrtCue[] → SRT 文件
    write_srt(output_path, cues)
