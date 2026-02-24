"""
Mix Processor 模块（唯一公共入口）

公共 API：
- run(): 旧版入口（使用拼接后的 TTS 音频）
- run_timeline(): 新版入口（Timeline-First Architecture，使用 adelay）

内部模块（不直接导入）：
- impl.py: 内部实现
"""
from .processor import run, run_timeline

__all__ = ["run", "run_timeline"]
