"""ASR / TTS provider 抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


# ── ASR ──────────────────────────────────────────────────────────────────────


class ASRProvider(ABC):
    """所有 ASR provider 的抽象基类。"""

    name: str = "base"

    # 输入类型：url / file
    # url: 需要签名 URL（doubao/qwen/tencent/paraformer）
    # file: 需要本地文件路径（openai/funasr/fish）
    # gcs: 需要 GCS 签名 URL（gemini）
    input_type: str = "url"

    @abstractmethod
    def transcribe(self, audio_input: str, **kwargs) -> dict:
        """
        转写音频文件，返回原始结果 dict。

        Args:
            audio_input: 音频 URL 或本地文件路径（取决于 input_type）
            **kwargs: provider 专属参数

        Returns:
            原始结果 dict（各 provider 格式不同，直接保存）
        """
        ...


# ── TTS ──────────────────────────────────────────────────────────────────────


@dataclass
class TTSRequest:
    speaker: str
    text: str
    voice: Optional[str] = None
    emotion: Optional[str] = None


class TTSProvider(ABC):
    """所有 TTS provider 的抽象基类。"""

    name: str = "base"

    @abstractmethod
    def synthesize(self, text: str, voice: str, **kwargs) -> bytes:
        """
        合成单句音频。

        Args:
            text: 要合成的文本
            voice: 音色 ID
            **kwargs: provider 专属参数

        Returns:
            音频字节（PCM 或 WAV）
        """
        ...
