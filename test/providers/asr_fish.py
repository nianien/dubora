"""Fish Audio ASR provider。

环境变量: FISH_API_KEY
"""

import os
import sys

from .base import ASRProvider


class FishASRProvider(ASRProvider):
    name = "fish"
    input_type = "file"

    def __init__(self, api_key=None):
        from fish_audio_sdk import Session

        key = api_key or os.getenv("FISH_API_KEY")
        if not key:
            raise RuntimeError("需要 FISH_API_KEY")
        self.session = Session(apikey=key)

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        from fish_audio_sdk import ASRRequest

        print("[INFO] Fish ASR...", file=sys.stderr)
        with open(audio_input, "rb") as f:
            result = self.session.asr(ASRRequest(audio=f.read(), ignore_timestamps=False))

        return result.model_dump()
