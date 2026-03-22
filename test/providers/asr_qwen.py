"""Qwen3-ASR provider — 阿里云百炼千问语音识别。

支持两种调用方式:
  - qwen3-asr-flash:           OpenAI-compatible API，同步
  - qwen3-asr-flash-filetrans: REST API 异步，支持大文件
"""

import os
import sys
import time
from typing import Optional

import requests as http_requests

from .base import ASRProvider

_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


class QwenASRProvider(ASRProvider):
    name = "qwen"
    input_type = "url"

    def __init__(self, api_key: Optional[str] = None, model_name: str = "qwen3-asr-flash-filetrans"):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise RuntimeError("需要 DASHSCOPE_API_KEY")
        self.model_name = model_name

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        if self.model_name.endswith("-filetrans"):
            return self._transcribe_filetrans(audio_input)
        return self._transcribe_openai(audio_input)

    def _transcribe_openai(self, audio_input: str) -> dict:
        from openai import OpenAI

        client = OpenAI(
            api_key=self.api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

        print(f"[INFO] Qwen ASR ({self.model_name})...", file=sys.stderr)
        completion = client.chat.completions.create(
            model=self.model_name,
            messages=[{
                "role": "user",
                "content": [{"type": "input_audio", "input_audio": {"data": audio_input}}],
            }],
            stream=False,
            extra_body={"asr_options": {"enable_itn": True}},
        )
        return completion.model_dump()

    def _transcribe_filetrans(self, audio_input: str) -> dict:
        print(f"[INFO] Qwen ASR filetrans ({self.model_name})...", file=sys.stderr)

        resp = http_requests.post(
            f"{_BASE_URL}/services/audio/asr/transcription",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            },
            json={
                "model": self.model_name,
                "input": {"file_url": audio_input},
                "parameters": {"enable_itn": True, "enable_words": True},
            },
            timeout=30,
        )
        resp.raise_for_status()
        task_id = resp.json()["output"]["task_id"]
        print(f"[INFO] task_id={task_id}", file=sys.stderr)

        while True:
            poll = http_requests.get(
                f"{_BASE_URL}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
            poll.raise_for_status()
            task = poll.json()
            status = task["output"]["task_status"]

            if status == "SUCCEEDED":
                break
            elif status == "FAILED":
                raise RuntimeError(f"Qwen ASR 失败: {task['output'].get('message')}")
            print(f"[INFO] 等待中... ({status})", file=sys.stderr)
            time.sleep(3)

        # 下载转录结果
        transcript_url = task["output"].get("result", {}).get("transcription_url")
        if transcript_url:
            tr = http_requests.get(transcript_url, timeout=30)
            tr.raise_for_status()
            task["transcript"] = tr.json()

        return task
