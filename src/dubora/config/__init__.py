"""Configuration and settings"""
import json
from pathlib import Path

_EMOTIONS_PATH = Path(__file__).parent / "emotions.json"


def load_emotions() -> list[dict]:
    """读取全局 emotions 配置。[{"name": "开心", "value": "happy", "lang": "zh,en"}, ...]"""
    with open(_EMOTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
