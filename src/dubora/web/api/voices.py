"""
Voices API: voice catalogue + TTS synthesis preview (with server-side cache)
"""
import hashlib
import json
import os
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dubora.pipeline.processors.tts.volcengine import _call_volcengine_tts

router = APIRouter()

_VOICES_PATH = Path(__file__).resolve().parents[4] / "resources" / "voices.json"
_CACHE_DIR = Path(__file__).resolve().parents[4] / ".cache" / "voice-preview"
_MANIFEST_PATH = _CACHE_DIR / "manifest.json"


# ── cache helpers ────────────────────────────────────────────────────────────


def _cache_key(voice_id: str, text: str, emotion: str) -> str:
    raw = f"{voice_id}|{text.strip()}|{emotion}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_manifest() -> List[Dict[str, Any]]:
    if not _MANIFEST_PATH.exists():
        return []
    with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(entries: List[Dict[str, Any]]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _MANIFEST_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    tmp.replace(_MANIFEST_PATH)


def _pcm_to_wav(
    pcm: bytes,
    sample_rate: int = 24000,
    bits: int = 16,
    channels: int = 1,
) -> bytes:
    """Prepend 44-byte RIFF/WAV header to raw PCM data."""
    data_size = len(pcm)
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm


# ── GET /api/voices ──────────────────────────────────────────────────────────


@router.get("/voices")
async def get_voices() -> List[Dict[str, Any]]:
    """Return voice catalogue parsed from voices.json (new format)."""
    with open(_VOICES_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    result = []
    for v in raw:
        voice_id = v.get("VoiceType", "")
        if not voice_id:
            continue

        # flatten categories
        categories: List[str] = []
        for cg in v.get("Categories", []):
            for c in cg.get("Categories", []):
                if c not in categories:
                    categories.append(c)

        # structured emotions → [{value, label, icon}]
        emotions = [
            {"value": e["Value"], "label": e.get("Label", ""), "icon": e.get("Icon", "")}
            for e in v.get("Emotions", [])
            if e.get("Value")
        ]

        # languages with sample text
        languages = [
            {"lang": l.get("Language", ""), "text": l.get("Text", ""), "flag": l.get("Flag", "")}
            for l in v.get("Languages", [])
        ]

        result.append(
            {
                "name": v.get("Name", voice_id),
                "voice_id": voice_id,
                "gender": v.get("Gender", ""),
                "age": v.get("Age", ""),
                "description": v.get("Description", ""),
                "avatar": v.get("Avatar", ""),
                "trial_url": v.get("TrialURL", ""),
                "categories": categories,
                "languages": languages,
                "emotions": emotions,
                "resource_id": v.get("ResourceID", ""),
            }
        )
    return result


# ── GET /api/voices/history ──────────────────────────────────────────────────


@router.get("/voices/history")
async def get_history() -> List[Dict[str, Any]]:
    """Return cached synthesis history (newest first)."""
    entries = _load_manifest()
    entries.reverse()
    return entries


# ── GET /api/voices/audio/{key} ──────────────────────────────────────────────


@router.get("/voices/audio/{key}")
async def get_audio(key: str) -> FileResponse:
    """Serve a cached WAV file."""
    wav_path = _CACHE_DIR / f"{key}.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(wav_path, media_type="audio/wav")


# ── POST /api/voices/synthesize ──────────────────────────────────────────────


class SynthesizeRequest(BaseModel):
    voice_id: str
    text: str
    emotion: Optional[str] = None


@router.post("/voices/synthesize")
async def synthesize_voice(req: SynthesizeRequest) -> Dict[str, Any]:
    """Synthesize TTS, cache on server, return metadata with audio URL."""
    emotion = req.emotion or ""
    key = _cache_key(req.voice_id, req.text, emotion)
    wav_path = _CACHE_DIR / f"{key}.wav"

    # cache hit → skip API call
    if not wav_path.exists():
        app_id = os.environ.get("DOUBAO_APPID", "")
        access_key = os.environ.get("DOUBAO_ACCESS_TOKEN", "")
        if not app_id or not access_key:
            raise HTTPException(
                status_code=500,
                detail="DOUBAO_APPID / DOUBAO_ACCESS_TOKEN not configured",
            )

        try:
            pcm_bytes, _ = _call_volcengine_tts(
                text=req.text,
                speaker=req.voice_id,
                app_id=app_id,
                access_key=access_key,
                emotion=req.emotion if req.emotion else None,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        wav_bytes = _pcm_to_wav(pcm_bytes)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = wav_path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(wav_bytes)
        tmp.replace(wav_path)

    # upsert manifest entry (avoid duplicates)
    entries = _load_manifest()
    if not any(e["key"] == key for e in entries):
        voice_name = req.voice_id
        try:
            with open(_VOICES_PATH, "r", encoding="utf-8") as f:
                for v in json.load(f):
                    if v.get("VoiceType") == req.voice_id:
                        voice_name = v.get("Name", voice_name)
                        break
        except Exception:
            pass

        entries.append(
            {
                "key": key,
                "voice_id": req.voice_id,
                "voice_name": voice_name,
                "emotion": emotion,
                "text": req.text.strip(),
                "created_at": datetime.now().isoformat(),
            }
        )
        _save_manifest(entries)

    return {
        "key": key,
        "audio_url": f"/api/voices/audio/{key}",
    }
