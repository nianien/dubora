"""Gemini TTS provider。"""

import os
import struct
from typing import Optional

from .base import TTSProvider

DEFAULT_VOICE = "Kore"
DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
SAMPLE_RATE = 24000


class GeminiTTSProvider(TTSProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = DEFAULT_MODEL,
    ):
        try:
            from google import genai
        except ImportError:
            raise ImportError(
                "google-genai 未安装，请执行: pip install google-genai"
            )

        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("Gemini TTS 需要 GEMINI_API_KEY 环境变量")

        self.client = genai.Client(api_key=key)
        self.model_name = model_name

    def synthesize(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        **kwargs,
    ) -> bytes:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice,
                        )
                    )
                ),
            ),
        )

        pcm_data = response.candidates[0].content.parts[0].inline_data.data
        return self._pcm_to_wav(pcm_data, SAMPLE_RATE)

    @staticmethod
    def _pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
        data_size = len(pcm)
        byte_rate = sample_rate * channels * bits // 8
        block_align = channels * bits // 8
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_size,
            b"WAVE",
            b"fmt ",
            16,
            1,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits,
            b"data",
            data_size,
        )
        return header + pcm
