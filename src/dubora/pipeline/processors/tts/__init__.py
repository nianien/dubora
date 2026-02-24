"""
TTS Processor 模块（唯一公共入口）

公共 API：
- run_per_segment(): Timeline-First Architecture，输出 per-segment WAVs

声线分配通过 speaker_to_role.json 解析，不再使用 assign_voices。

内部模块：
- volcengine.py: VolcEngine TTS 实现
"""
from .processor import run_per_segment

__all__ = ["run_per_segment"]
