"""
Fish Audio TTS client: standalone function using fish-audio-sdk.

Voice cloning mode: uses reference audio to clone speaker voice.
Requires: pip install fish-audio-sdk
Env: FISH_API_KEY
"""
import time
from typing import Optional


# 视为瞬时网络故障、可重试的异常类名（基于类名匹配，避免硬依赖 httpx）。
# Fish SDK 底层走 httpx，常见瞬时错误：RemoteProtocolError / ReadError /
# ConnectError / ConnectTimeout / ReadTimeout / WriteError。
_TRANSIENT_EXC_NAMES = {
    "RemoteProtocolError", "ReadError", "WriteError", "ConnectError",
    "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectionError", "ProtocolError", "IncompleteRead",
}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return type(exc).__name__ in _TRANSIENT_EXC_NAMES


def call_fish_tts(
    text: str,
    api_key: str,
    reference_id: Optional[str] = None,
    reference_audio: Optional[str] = None,
    max_retries: int = 3,
) -> bytes:
    """
    Call Fish Audio TTS API via SDK.

    Uses voice cloning: either a pre-uploaded reference_id or a local reference_audio file.

    Retries on transient network errors (connection drop / timeout / protocol error).
    Other errors (4xx, missing reference, etc.) are raised immediately.

    Args:
        text: Text to synthesize
        api_key: Fish Audio API key
        reference_id: Fish Audio voice model ID (pre-uploaded reference)
        reference_audio: Path to local reference audio file for on-the-fly cloning
        max_retries: Max attempts on transient errors (default 3)

    Returns:
        Audio bytes (WAV/MP3 stream from SDK)
    """
    try:
        from fish_audio_sdk import Session, TTSRequest
    except ImportError:
        raise ImportError(
            "fish-audio-sdk not installed. Run: pip install fish-audio-sdk"
        )

    session = Session(apikey=api_key)

    req_kwargs = {"text": text}

    if reference_id:
        req_kwargs["reference_id"] = reference_id
    elif reference_audio:
        import os
        if not os.path.exists(reference_audio):
            raise FileNotFoundError(f"Reference audio not found: {reference_audio}")
        from fish_audio_sdk import ReferenceAudio
        with open(reference_audio, "rb") as f:
            ref_bytes = f.read()
        req_kwargs["references"] = [
            ReferenceAudio(audio=ref_bytes, text="")
        ]

    last_err: Optional[BaseException] = None
    for attempt in range(max_retries):
        try:
            chunks = []
            for chunk in session.tts(TTSRequest(**req_kwargs)):
                chunks.append(chunk)
            audio_bytes = b"".join(chunks)
            if not audio_bytes:
                raise RuntimeError("No audio data received from Fish Audio TTS API")
            return audio_bytes
        except Exception as e:
            if not _is_transient(e) or attempt == max_retries - 1:
                raise
            last_err = e
            time.sleep(2 ** attempt)  # 1s, 2s

    # 防御性：理论不会到这里
    raise RuntimeError(f"Fish TTS exhausted retries: {last_err}")
