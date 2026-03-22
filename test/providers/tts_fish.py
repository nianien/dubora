"""Fish Speech TTS provider — 支持 Fish Audio SDK 和 SiliconFlow API 两种后端。

Fish Audio SDK (默认):
  pip install fish-audio-sdk
  环境变量: FISH_API_KEY

SiliconFlow API:
  环境变量: SILICONFLOW_API_KEY
  支持 Fish Speech V1.5 模型，声音克隆，语速控制
"""

import base64
import os
from typing import Optional

from .base import TTSProvider

# SiliconFlow 预置音色
SF_PRESET_VOICES = {
    "alex": "fishaudio/fish-speech-1.5:alex",        # 男 沉稳
    "benjamin": "fishaudio/fish-speech-1.5:benjamin",  # 男 低沉
    "charles": "fishaudio/fish-speech-1.5:charles",    # 男 磁性
    "david": "fishaudio/fish-speech-1.5:david",        # 男 活泼
    "anna": "fishaudio/fish-speech-1.5:anna",          # 女 沉稳
    "bella": "fishaudio/fish-speech-1.5:bella",        # 女 热情
    "claire": "fishaudio/fish-speech-1.5:claire",      # 女 温柔
    "diana": "fishaudio/fish-speech-1.5:diana",        # 女 活泼
}


class FishTTSProvider(TTSProvider):
    name = "fish"

    def __init__(
        self,
        api_key: Optional[str] = None,
        reference_id: Optional[str] = None,
        backend: str = "fish-audio",
    ):
        self.backend = backend
        self.reference_id = reference_id

        if backend == "siliconflow":
            self.api_key = api_key or os.getenv("SILICONFLOW_API_KEY")
            self.base_url = os.getenv(
                "SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1"
            )
            if not self.api_key:
                raise RuntimeError("SiliconFlow 后端需要 SILICONFLOW_API_KEY 环境变量")
        else:
            try:
                from fish_audio_sdk import Session
            except ImportError:
                raise ImportError(
                    "fish-audio-sdk 未安装，请执行: pip install fish-audio-sdk"
                )

            key = api_key or os.getenv("FISH_API_KEY")
            if not key:
                raise RuntimeError("Fish TTS 需要 FISH_API_KEY 环境变量")

            self.session = Session(api_key=key)

    def synthesize(
        self,
        text: str,
        voice: str = "",
        **kwargs,
    ) -> bytes:
        if self.backend == "siliconflow":
            return self._synthesize_siliconflow(text, voice, **kwargs)
        return self._synthesize_fish_audio(text, voice, **kwargs)

    def _synthesize_fish_audio(self, text: str, voice: str, **kwargs) -> bytes:
        from fish_audio_sdk import TTSRequest

        ref_id = voice or self.reference_id
        req_kwargs = {"text": text}
        if ref_id:
            req_kwargs["reference_id"] = ref_id

        chunks = []
        for chunk in self.session.tts(TTSRequest(**req_kwargs)):
            chunks.append(chunk)

        return b"".join(chunks)

    def _synthesize_siliconflow(self, text: str, voice: str, **kwargs) -> bytes:
        import requests as http_requests

        # 解析音色: 短名 → 全名
        resolved_voice = SF_PRESET_VOICES.get(voice, voice) if voice else ""

        payload = {
            "model": "fishaudio/fish-speech-1.5",
            "input": text,
            "voice": resolved_voice or "fishaudio/fish-speech-1.5:anna",
            "response_format": "wav",
            "sample_rate": 24000,
            "stream": False,
        }

        speed = kwargs.get("speed")
        if speed and speed != 1.0:
            payload["speed"] = speed

        gain = kwargs.get("gain")
        if gain:
            payload["gain"] = gain

        # 动态声音克隆: ref_audio + ref_text
        ref_audio = kwargs.get("ref_audio")
        if ref_audio:
            payload["voice"] = ""
            ref_entry = {}

            if os.path.isfile(ref_audio):
                with open(ref_audio, "rb") as f:
                    audio_b64 = base64.b64encode(f.read()).decode()
                ext = os.path.splitext(ref_audio)[1].lstrip(".")
                mime = {
                    "wav": "audio/wav",
                    "mp3": "audio/mpeg",
                    "opus": "audio/opus",
                    "flac": "audio/flac",
                }.get(ext, "audio/wav")
                ref_entry["audio"] = f"data:{mime};base64,{audio_b64}"
            else:
                # URL
                ref_entry["audio"] = ref_audio

            ref_text = kwargs.get("ref_text", "")
            if ref_text:
                ref_entry["text"] = ref_text
            payload["references"] = [ref_entry]

        resp = http_requests.post(
            f"{self.base_url}/audio/speech",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.content
