"""ASR Processor 模块

公共 API：
- transcribe(): Doubao ASR 调用
- get_doubao_utterances / fill_null_emotions / extend_end_ms: parse 后处理

内部模块：
- impl.py: Doubao ASR 实现
- postprocess.py: Doubao 输出 → 统一 utt 结构 + emotion 回填 + end_ms 延长
"""
from .impl import transcribe
from .postprocess import get_doubao_utterances, fill_null_emotions, extend_end_ms

__all__ = ["transcribe", "get_doubao_utterances", "fill_null_emotions", "extend_end_ms"]
