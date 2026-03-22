"""
ASR Processor 模块

公共 API：
- transcribe(): Doubao ASR 调用

内部模块：
- impl.py: Doubao ASR 实现
- fusion.py: 后处理算法（emotion 回填、end_ms 延长等）
"""
from .impl import transcribe

__all__ = ["transcribe"]
