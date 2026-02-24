"""
Core package for video subtitle pipeline.

Pipeline:
    Video
      ↓
    ffmpeg extract audio
      ↓
    Whisper ASR (Chinese segments)
      ↓
    (optional) text cleaning
      ↓
    Gemini translation (English)
      ↓
    Output zh.srt + en.srt + ASS (safe area)
      ↓
    ffmpeg burn-in or external subtitles
"""

from .config.settings import load_env_file

__all__ = ["load_env_file"]

