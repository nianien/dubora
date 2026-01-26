"""
字幕处理模块（唯一公共入口）

职责：
- 根据 ASR 结果生成字幕文件（segments.json, srt）
- ASR 后处理（切句、合并、speaker 处理）
- 文本清理（标点处理）
- SRT 渲染和解析

公共 API：
- generate_subtitles(): 主要入口，生成字幕文件
- generate_subtitles_from_preset(): 从预设生成字幕（向后兼容）
- parse_srt(): 解析 SRT 文件

内部模块（不直接导入）：
- asr_post.py: 切句/合并/speaker 处理/文本清理
- profiles.py: 策略配置
- srt.py: SRT 格式处理（编解码）
- types.py: 通用数据结构
"""
from .subtitles import (
    generate_subtitles,
    generate_subtitles_from_preset,
    resolve_cached_subtitles,
    segments_path,
    srt_path,
)
from .srt import parse_srt
from .asr_post import speaker_aware_postprocess
from .profiles import POSTPROFILES

# 导出公共 API（其他模块作为内部实现）
__all__ = [
    "generate_subtitles",
    "generate_subtitles_from_preset",
    "resolve_cached_subtitles",
    "segments_path",
    "srt_path",
    "parse_srt",
    "speaker_aware_postprocess",
    "POSTPROFILES",
]
