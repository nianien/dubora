"""
SRT 格式处理：Segment ↔ SRT 文件

职责：
- Segment[] → SrtCue[]（去掉 speaker）
- 写入 SRT 文件
- 解析 SRT 文件 → SrtCue[]

这是 SRT 格式的编解码逻辑，围绕 SRT 格式的内聚。
同一类变化（SRT 格式规则变化）只改这个模块。

格式模块只认 cues，不关心 pipeline Segment（保持内聚）。
"""
import re
from pathlib import Path
from typing import List

from pikppo.schema import Segment, SrtCue
from pikppo.utils.timecode import write_srt_from_segments


def segments_to_srt_cues(segments: List[Segment]) -> List[SrtCue]:
    """
    将 Segment[] 转换为 SrtCue[]（去掉 speaker）。
    
    Args:
        segments: 带 speaker 的 Segment 列表
    
    Returns:
        不带 speaker 的 SrtCue 列表
    """
    return [
        SrtCue(
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            text=seg.text,  # 不包含 speaker 标签
        )
        for seg in segments
    ]


def write_srt(path: Path, cues: List[SrtCue]) -> None:
    """
    将 SrtCue[] 写入 SRT 文件。
    
    Args:
        path: 输出文件路径（Path）
        cues: SrtCue 列表
    """
    # 转换为字典格式（供 write_srt_from_segments 使用）
    segments = [
        {
            "start": cue.start_ms / 1000.0,  # 毫秒转秒
            "end": cue.end_ms / 1000.0,
            "text": cue.text,
        }
        for cue in cues
    ]
    
    write_srt_from_segments(segments, str(path), text_key="text")


def parse_srt(path: Path) -> List[SrtCue]:
    """
    解析 SRT 文件，返回 SrtCue[]。
    
    Args:
        path: SRT 文件路径（Path）
    
    Returns:
        SrtCue 列表
    
    SRT 格式示例：
        1
        00:00:01,234 --> 00:00:03,456
        这是第一行字幕
        这是第二行字幕
        
        2
        00:00:04,567 --> 00:00:06,789
        这是第二段字幕
    """
    if not path.exists():
        raise FileNotFoundError(f"SRT file not found: {path}")
    
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 使用正则表达式匹配 SRT 块
    # 格式：序号\n时间轴\n文本（可能多行）\n空行
    pattern = r'(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n\d+\s*\n|\n*$)'
    matches = re.finditer(pattern, content, re.MULTILINE | re.DOTALL)
    
    cues = []
    for match in matches:
        index = match.group(1)
        start_str = match.group(2)
        end_str = match.group(3)
        text = match.group(4).strip()
        
        # 转换时间格式：HH:MM:SS,mmm -> 毫秒（int）
        start_ms = int(_srt_time_to_seconds(start_str) * 1000)
        end_ms = int(_srt_time_to_seconds(end_str) * 1000)
        
        # 清理文本（移除多余的换行和空格）
        text = re.sub(r'\s+', ' ', text).strip()
        
        if text:  # 只添加非空文本
            cues.append(SrtCue(
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            ))
    
    return cues


def _srt_time_to_seconds(time_str: str) -> float:
    """
    将 SRT 时间格式 (HH:MM:SS,mmm) 转换为秒（float）。
    
    Args:
        time_str: SRT 时间字符串，例如 "00:01:23,456"
    
    Returns:
        秒数（float），例如 83.456
    """
    # 格式：HH:MM:SS,mmm
    parts = time_str.split(',')
    if len(parts) != 2:
        raise ValueError(f"Invalid SRT time format: {time_str}")
    
    time_part = parts[0]  # HH:MM:SS
    ms_part = int(parts[1])  # mmm
    
    time_parts = time_part.split(':')
    if len(time_parts) != 3:
        raise ValueError(f"Invalid SRT time format: {time_str}")
    
    hours = int(time_parts[0])
    minutes = int(time_parts[1])
    seconds = int(time_parts[2])
    
    total_seconds = hours * 3600 + minutes * 60 + seconds + ms_part / 1000.0
    return total_seconds
