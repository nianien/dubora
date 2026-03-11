"""
VolcEngine TTS HTTP client: standalone function, only depends on requests + stdlib.

Extracted from pipeline/processors/tts/volcengine.py so that both web (voice preview)
and pipeline (full TTS synthesis) can use it without cross-package imports.
"""
import base64
import json
import os
import uuid
from typing import Any, Dict, Optional

import requests


# VolcEngine API configuration
VOLC_API_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_RESOURCE_ID = "seed-tts-1.0"
DEFAULT_FORMAT = "pcm"
DEFAULT_SAMPLE_RATE = 24000


def call_volcengine_tts(
    text: str,
    speaker: str,
    app_id: str,
    access_key: str,
    resource_id: str = DEFAULT_RESOURCE_ID,
    format: str = DEFAULT_FORMAT,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    speech_rate: float = 0.0,
    speed_ratio: float = 1.0,
    emotion: Optional[str] = None,
    emotion_scale: int = 4,
    enable_timestamp: bool = False,
    enable_subtitle: bool = False,
    reference_audio: Optional[str] = None,
    **kwargs,
) -> tuple[bytes, Optional[Dict[str, Any]]]:
    """
    Call VolcEngine TTS API (streaming).

    Args:
        text: Text to synthesize
        speaker: Speaker ID (voice)
        app_id: VolcEngine APP ID
        access_key: VolcEngine Access Key
        resource_id: Resource ID (e.g., "seed-tts-1.0", "seed-tts-2.0", "seed-tts-icl-2.0")
        format: Audio format (mp3/ogg_opus/pcm)
        sample_rate: Sample rate
        speech_rate: (deprecated) Speech rate (-50 to 100, 0 = normal)
        speed_ratio: Speed ratio (0.1~2.0, 1.0 = normal)
        emotion: Emotion label (optional)
        emotion_scale: Emotion scale (1-5, default 4)
        reference_audio: Path to reference audio for ICL voice cloning (optional).
        **kwargs: Other parameters

    Returns:
        (audio_bytes, sentence_data) tuple
    """
    request_id = str(uuid.uuid4())

    # ICL mode
    if reference_audio and os.path.exists(reference_audio):
        resource_id = kwargs.pop("icl_resource_id", "seed-tts-icl-2.0")
        with open(reference_audio, "rb") as rf:
            ref_bytes = rf.read()
        ref_audio_b64 = base64.b64encode(ref_bytes).decode("utf-8")
        print(f"  ICL mode: reference_audio={reference_audio} ({len(ref_bytes)} bytes), resource_id={resource_id}")
    else:
        ref_audio_b64 = None

    # Build request body
    body: Dict[str, Any] = {
        "user": {
            "uid": kwargs.get("uid", "dubora_user")
        },
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {
                "format": format,
                "sample_rate": sample_rate,
            }
        }
    }

    if ref_audio_b64:
        body["req_params"]["reference_audio"] = ref_audio_b64

    if speed_ratio != 1.0:
        body["req_params"]["audio_params"]["speed_ratio"] = speed_ratio
    elif speech_rate != 0.0:
        body["req_params"]["audio_params"]["speech_rate"] = speech_rate

    if emotion:
        body["req_params"]["audio_params"]["emotion"] = emotion
        body["req_params"]["audio_params"]["emotion_scale"] = emotion_scale

    if enable_timestamp:
        body["req_params"]["audio_params"]["enable_timestamp"] = True
    if enable_subtitle:
        body["req_params"]["audio_params"]["enable_subtitle"] = True

    if "additions" in kwargs:
        body["req_params"]["additions"] = kwargs["additions"]

    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Id": app_id,
        "X-Api-Access-Key": access_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
    }

    session = requests.Session()
    response = session.post(
        VOLC_API_URL,
        headers=headers,
        json=body,
        stream=True,
        timeout=60,
    )

    response.raise_for_status()

    audio_data = bytearray()
    sentence_data = None
    chunk_count = 0
    total_audio_size = 0

    for chunk in response.iter_lines(decode_unicode=True):
        if not chunk:
            continue

        try:
            data = json.loads(chunk)
            code = data.get("code", 0)

            if code == 0 and "data" in data and data["data"]:
                try:
                    chunk_audio = base64.b64decode(data["data"])
                    audio_size = len(chunk_audio)
                    total_audio_size += audio_size
                    audio_data.extend(chunk_audio)
                    chunk_count += 1
                    if chunk_count <= 5:
                        print(f"  Chunk {chunk_count}: decoded {audio_size} bytes, total: {total_audio_size} bytes")
                except Exception as e:
                    print(f"  Failed to decode chunk {chunk_count + 1}: {e}")
                    continue

            if code == 0 and "sentence" in data and data["sentence"]:
                sentence_data = data.get("sentence")
                print(f"  Received sentence data")

            if code == 20000000:
                if 'usage' in data:
                    print(f"  Usage: {data['usage']}")
                print(f"  Received end marker (code=20000000), total chunks: {chunk_count}, total audio: {total_audio_size} bytes")
                break

            if code > 0 and code != 20000000:
                print(f"  Error response: {data}")
                message = data.get("message", "Unknown error")
                raise RuntimeError(f"VolcEngine TTS API error: code={code}, message={message}")

        except json.JSONDecodeError as e:
            print(f"  JSON decode error: {e}, chunk: {chunk[:100]}")
            continue

    if not audio_data:
        raise RuntimeError("No audio data received from VolcEngine TTS API")

    audio_bytes = bytes(audio_data)
    print(f"  Final audio: {len(audio_bytes)} bytes from {chunk_count} chunks")

    return audio_bytes, sentence_data
