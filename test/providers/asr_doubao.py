"""Doubao ASR provider — 复用 dubora_pipeline 现有实现。"""

import os
import sys
from typing import Optional

from .base import ASRProvider


class DoubaoASRProvider(ASRProvider):
    name = "doubao"
    input_type = "url"

    def __init__(self, appid: Optional[str] = None, access_token: Optional[str] = None):
        self.appid = appid or os.getenv("DOUBAO_APPID")
        self.access_token = access_token or os.getenv("DOUBAO_ACCESS_TOKEN")
        if not self.appid or not self.access_token:
            raise RuntimeError("需要 DOUBAO_APPID 和 DOUBAO_ACCESS_TOKEN")

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        from dubora_pipeline.processors.asr.impl import transcribe

        print(f"[INFO] Doubao ASR (preset={kwargs.get('preset', 'asr_vad_spk')})...", file=sys.stderr)
        raw, _ = transcribe(
            audio_url=audio_input,
            preset=kwargs.get("preset", "asr_vad_spk"),
            appid=self.appid,
            access_token=self.access_token,
            hotwords=kwargs.get("hotwords"),
            language=kwargs.get("language", "zh-CN"),
        )
        return raw
