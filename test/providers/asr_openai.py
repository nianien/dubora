"""OpenAI ASR provider — gpt-4o-transcribe / whisper-1。

文件大小限制 25MB。
需要环境变量：OPENAI_API_KEY（或 OPENAI_KEY）
"""

import os
import sys
from pathlib import Path
from typing import Optional

from .base import ASRProvider

# 各模型支持的 response_format：
#   gpt-4o-transcribe-diarize → diarized_json
#   whisper-1                 → verbose_json (含 word-level 时间戳)
#   gpt-4o-transcribe / mini  → json
_FORMAT_MAP = {
    "gpt-4o-transcribe-diarize": "diarized_json",
    "whisper-1": "verbose_json",
}


class OpenAIASRProvider(ASRProvider):
    name = "openai"
    input_type = "file"

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gpt-4o-transcribe-diarize"):
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("需要 OPENAI_API_KEY")

        self.client = OpenAI(api_key=key)
        self.model_name = model_name

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        path = Path(audio_input)
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > 25:
            print(f"[WARN] 文件 {size_mb:.1f}MB 超过 25MB 限制", file=sys.stderr)

        fmt = _FORMAT_MAP.get(self.model_name, "json")
        print(f"[INFO] OpenAI ASR ({self.model_name}, {size_mb:.1f}MB, {fmt})...", file=sys.stderr)

        with open(path, "rb") as f:
            transcript = self.client.audio.transcriptions.create(
                model=self.model_name,
                file=f,
                response_format=fmt,
            )

        return transcript.model_dump()
