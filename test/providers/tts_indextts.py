"""IndexTTS2 TTS provider — 支持 fal.ai 云端 API 和 SiliconFlow 云端 API。

fal.ai (推荐):
  pip install fal-client
  环境变量: FAL_KEY
  定价: $0.002/音频秒

SiliconFlow:
  环境变量: SILICONFLOW_API_KEY
  定价: $7.15/M UTF-8 bytes
"""

import os
from typing import Optional

from .base import TTSProvider

# ASR emotion 字符串 → fal.ai emotional_strengths dict
# 8 维: happy, angry, sad, afraid, disgusted, melancholic, surprised, calm
EMOTION_MAP = {
    "happy": {"happy": 0.9},
    "angry": {"angry": 0.9},
    "sad": {"sad": 0.9},
    "fearful": {"afraid": 0.9},
    "fear": {"afraid": 0.9},
    "afraid": {"afraid": 0.9},
    "disgusted": {"disgusted": 0.9},
    "melancholic": {"melancholic": 0.9},
    "surprised": {"surprised": 0.9},
    "surprise": {"surprised": 0.9},
    "calm": {"calm": 0.9},
    "neutral": {"calm": 0.5},
}

# 同上, 但是 list 格式 (用于 SiliconFlow)
EMOTION_VECTORS = {
    "happy": [0.9, 0, 0, 0, 0, 0, 0, 0],
    "angry": [0, 0.9, 0, 0, 0, 0, 0, 0],
    "sad": [0, 0, 0.9, 0, 0, 0, 0, 0],
    "fearful": [0, 0, 0, 0.9, 0, 0, 0, 0],
    "fear": [0, 0, 0, 0.9, 0, 0, 0, 0],
    "afraid": [0, 0, 0, 0.9, 0, 0, 0, 0],
    "disgusted": [0, 0, 0, 0, 0.9, 0, 0, 0],
    "melancholic": [0, 0, 0, 0, 0, 0.9, 0, 0],
    "surprised": [0, 0, 0, 0, 0, 0, 0.9, 0],
    "surprise": [0, 0, 0, 0, 0, 0, 0.9, 0],
    "calm": [0, 0, 0, 0, 0, 0, 0, 0.9],
    "neutral": [0, 0, 0, 0, 0, 0, 0, 0.5],
}


class IndexTTSProvider(TTSProvider):
    name = "indextts"

    def __init__(
        self,
        backend: str = "fal",
        api_key: Optional[str] = None,
    ):
        self.backend = backend

        if backend == "fal":
            try:
                import fal_client  # noqa: F401
            except ImportError:
                raise ImportError(
                    "fal.ai 后端需要 fal-client: pip install fal-client"
                )
            self._fal = fal_client
            key = api_key or os.getenv("FAL_KEY")
            if not key:
                raise RuntimeError("fal.ai 后端需要 FAL_KEY 环境变量")
            os.environ.setdefault("FAL_KEY", key)

        elif backend == "siliconflow":
            self.api_key = api_key or os.getenv("SILICONFLOW_API_KEY")
            self.base_url = os.getenv(
                "SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1"
            )
            if not self.api_key:
                raise RuntimeError("SiliconFlow 后端需要 SILICONFLOW_API_KEY 环境变量")

        else:
            raise ValueError(f"未知后端: {backend}，可选: fal, siliconflow")

    def synthesize(
        self,
        text: str,
        voice: str = "",
        **kwargs,
    ) -> bytes:
        if self.backend == "fal":
            return self._synthesize_fal(text, voice, **kwargs)
        return self._synthesize_siliconflow(text, voice, **kwargs)

    # ── fal.ai ──────────────────────────────────────────

    def _upload_if_local(self, path_or_url: str) -> str:
        """本地文件上传到 fal.ai 存储，URL 原样返回。"""
        if not path_or_url:
            return ""
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        if os.path.isfile(path_or_url):
            print(f"[INFO] 上传文件到 fal.ai: {path_or_url}")
            return self._fal.upload_file(path_or_url)
        return path_or_url

    def _resolve_emotion_strengths(self, emotion=None, emo_vector=None):
        """→ fal.ai emotional_strengths dict，或 None。"""
        if emo_vector and isinstance(emo_vector, list):
            keys = ["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]
            return {k: v for k, v in zip(keys, emo_vector) if v > 0}
        if emotion and emotion in EMOTION_MAP:
            return EMOTION_MAP[emotion]
        return None

    def _synthesize_fal(self, text: str, voice: str, **kwargs) -> bytes:
        import requests as http_requests

        arguments = {"prompt": text}

        # 声音克隆: voice = 参考音频路径/URL
        audio_url = self._upload_if_local(voice)
        if audio_url:
            arguments["audio_url"] = audio_url

        # 情绪向量
        emo_strengths = self._resolve_emotion_strengths(
            kwargs.get("emotion"),
            kwargs.get("emo_vector"),
        )
        if emo_strengths:
            arguments["emotional_strengths"] = emo_strengths
            arguments["strength"] = kwargs.get("emo_alpha", 0.8)

        # 情绪参考音频
        emo_audio = kwargs.get("emo_audio")
        if emo_audio:
            arguments["emotional_audio_url"] = self._upload_if_local(emo_audio)
            arguments["strength"] = kwargs.get("emo_alpha", 0.8)

        # 文本推断情绪
        if kwargs.get("use_emo_text"):
            arguments["should_use_prompt_for_emotion"] = True
            if kwargs.get("emo_text"):
                arguments["emotion_prompt"] = kwargs["emo_text"]

        print(f"[INFO] fal.ai IndexTTS2 合成中...")
        result = self._fal.subscribe(
            "fal-ai/index-tts-2/text-to-speech",
            arguments=arguments,
        )

        # 下载生成的音频
        audio_info = result["audio"]
        print(f"[INFO] 下载音频: {audio_info.get('file_size', '?')} bytes")
        audio_resp = http_requests.get(audio_info["url"], timeout=60)
        audio_resp.raise_for_status()
        return audio_resp.content

    # ── SiliconFlow ─────────────────────────────────────

    def _synthesize_siliconflow(self, text: str, voice: str, **kwargs) -> bytes:
        import base64
        import requests as http_requests

        payload = {
            "model": "IndexTeam/IndexTTS-2",
            "input": text,
            "voice": "",
            "response_format": "wav",
            "sample_rate": 24000,
            "stream": False,
        }

        speed = kwargs.get("speed")
        if speed and speed != 1.0:
            payload["speed"] = speed

        # 声音克隆: 本地文件 → base64, URL 原样传递
        if voice:
            if os.path.isfile(voice):
                with open(voice, "rb") as f:
                    audio_b64 = base64.b64encode(f.read()).decode()
                ext = os.path.splitext(voice)[1].lstrip(".")
                mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "opus": "audio/opus"}.get(ext, "audio/wav")
                payload["references"] = [{"audio": f"data:{mime};base64,{audio_b64}"}]
            elif voice.startswith(("http://", "https://")):
                payload["references"] = [{"audio": voice}]
            else:
                payload["voice"] = voice

        resp = http_requests.post(
            f"{self.base_url}/audio/speech",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content
