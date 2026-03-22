"""Paraformer ASR provider — 阿里云百炼 paraformer-v2。

需要公网可访问的音频 URL。
"""

import os
import sys
import time
from typing import Optional

import requests as http_requests

from .base import ASRProvider

_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


class ParaformerASRProvider(ASRProvider):
    name = "paraformer"
    input_type = "url"

    def __init__(self, api_key: Optional[str] = None, model_name: str = "paraformer-v2"):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise RuntimeError("需要 DASHSCOPE_API_KEY")
        self.model_name = model_name

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        import dashscope
        from dashscope.audio.asr import Transcription

        dashscope.api_key = self.api_key
        dashscope.base_http_api_url = _BASE_URL

        print(f"[INFO] Paraformer ASR ({self.model_name})...", file=sys.stderr)
        task_response = Transcription.async_call(
            model=self.model_name,
            file_urls=[audio_input],
            diarization_enabled=True,
            speaker_count=0,
        )

        if task_response.status_code != 200:
            raise RuntimeError(f"提交失败: {task_response.message}")

        task_id = task_response.output.task_id
        print(f"[INFO] task_id={task_id}", file=sys.stderr)

        while True:
            result = Transcription.fetch(task=task_id)
            status = result.output.task_status
            if status == "SUCCEEDED":
                break
            elif status == "FAILED":
                raise RuntimeError(f"失败: {getattr(result.output, 'message', 'Unknown')}")
            print(f"[INFO] 等待中... ({status})", file=sys.stderr)
            time.sleep(3)

        # 下载转录结果
        task_results = getattr(result.output, "results", None) or []
        output = {"task_id": task_id, "transcripts": []}
        for item in task_results:
            url = item.get("transcription_url")
            if url:
                resp = http_requests.get(url, timeout=30)
                resp.raise_for_status()
                output["transcripts"].append(resp.json())

        return output
