"""
Doubao ASR 模块

提供豆包大模型 ASR 的完整功能：
- API 客户端（client）
- 预设配置（presets）
- 数据解析（parser，lossless）

职责边界：
- 只负责"外部事实采集与还原"
- 不负责字幕后处理（切句、合并、标点清理等）
- 不负责字幕渲染（SRT 格式化等）
- 字幕后处理和渲染由 pipeline/processors/srt 负责
"""
from .client import DoubaoASRClient, guess_audio_format
from .presets import get_preset, get_presets
from .request_types import RequestConfig, AudioConfig, DoubaoASRRequest, UserInfo, CorpusConfig
from .parser import parse_utterances

__all__ = [
    "DoubaoASRClient",
    "guess_audio_format",
    "get_preset",
    "get_presets",
    "RequestConfig",
    "AudioConfig",
    "DoubaoASRRequest",
    "UserInfo",
    "CorpusConfig",
    "parse_utterances",
]
