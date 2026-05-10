"""
Fish Audio TTS client: standalone function using fish-audio-sdk.

Voice cloning mode: uses reference audio to clone speaker voice.
Requires: pip install fish-audio-sdk
Env: FISH_API_KEY
"""
from typing import Optional


def call_fish_tts(
    text: str,
    api_key: str,
    reference_id: Optional[str] = None,
    reference_audio: Optional[str] = None,
) -> bytes:
    """
    Call Fish Audio TTS API via SDK.

    Uses voice cloning: either a pre-uploaded reference_id or a local reference_audio file.

    Args:
        text: Text to synthesize
        api_key: Fish Audio API key
        reference_id: Fish Audio voice model ID (pre-uploaded reference)
        reference_audio: Path to local reference audio file for on-the-fly cloning

    Returns:
        Audio bytes (WAV/MP3 stream from SDK)
    """
    try:
        from fish_audio_sdk import Session, TTSRequest
    except ImportError:
        raise ImportError(
            "fish-audio-sdk not installed. Run: pip install fish-audio-sdk"
        )

    session = Session(api_key=api_key)

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

    chunks = []
    for chunk in session.tts(TTSRequest(**req_kwargs)):
        chunks.append(chunk)

    audio_bytes = b"".join(chunks)
    if not audio_bytes:
        raise RuntimeError("No audio data received from Fish Audio TTS API")

    return audio_bytes
