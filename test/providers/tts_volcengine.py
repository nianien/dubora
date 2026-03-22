"""VolcEngine TTS provider — 复用 dubora_core 现有实现。"""

import os
import struct
from typing import Optional

from .base import TTSProvider

DEFAULT_VOICE = "zh_female_shuangkuaisisi_moon_bigtts"


class VolcEngineTTSProvider(TTSProvider):
    name = "volcengine"

    def __init__(
        self,
        app_id: Optional[str] = None,
        access_key: Optional[str] = None,
        resource_id: str = "seed-tts-1.0",
        sample_rate: int = 24000,
    ):
        self.app_id = app_id or os.getenv("DOUBAO_APPID")
        self.access_key = access_key or os.getenv("DOUBAO_ACCESS_TOKEN")
        if not self.app_id or not self.access_key:
            raise RuntimeError(
                "VolcEngine TTS 需要 DOUBAO_APPID 和 DOUBAO_ACCESS_TOKEN 环境变量"
            )
        self.resource_id = resource_id
        self.sample_rate = sample_rate

    def synthesize(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        *,
        emotion: Optional[str] = None,
        speed_ratio: float = 1.0,
        **kwargs,
    ) -> bytes:
        from dubora_core.infra.tts_client import call_volcengine_tts

        pcm_data, _ = call_volcengine_tts(
            text=text,
            speaker=voice,
            app_id=self.app_id,
            access_key=self.access_key,
            resource_id=self.resource_id,
            sample_rate=self.sample_rate,
            emotion=emotion,
            speed_ratio=speed_ratio,
            **kwargs,
        )
        return self._pcm_to_wav(pcm_data, self.sample_rate)

    @staticmethod
    def _pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
        """PCM → WAV (RIFF header)。"""
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
            1,  # PCM
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits,
            b"data",
            data_size,
        )
        return header + pcm
