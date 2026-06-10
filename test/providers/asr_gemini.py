"""Gemini 多模态 ASR provider — 与 pipeline 使用相同 prompt。"""

import json
import os
import sys
from typing import Optional

from .base import ASRProvider


class GeminiASRProvider(ASRProvider):
    name = "gemini"
    # 用本地文件 inline bytes：AI Studio key 模式下 Gemini 不能 fetch 任意 HTTPS
    # URL（包括 GCS signed URL），gs:// URI 也只在 Vertex AI 鉴权下可用。
    input_type = "file"

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-3.0-pro-preview"):
        from google import genai

        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("需要 GEMINI_API_KEY")

        self.client = genai.Client(api_key=key)
        self.model_name = model_name

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        from pathlib import Path
        from google.genai import types
        from dubora_pipeline.prompts import load_prompt

        prompt = load_prompt("asr_gemini")
        audio_bytes = Path(audio_input).read_bytes()

        print(f"[INFO] Gemini ASR ({self.model_name})...", file=sys.stderr)
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[
                types.Content(parts=[
                    types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                    types.Part.from_text(text=prompt.user),
                ])
            ],
            config=types.GenerateContentConfig(
                system_instruction=prompt.system,
                response_mime_type="application/json",
            ),
        )

        return json.loads(response.text)
