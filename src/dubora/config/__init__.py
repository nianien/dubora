"""Configuration and settings"""
import json
from pathlib import Path


def _find_project_root() -> Path:
    """向上查找 pyproject.toml 定位项目根目录。"""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise FileNotFoundError("Cannot find project root (no pyproject.toml found)")


PROJECT_ROOT = _find_project_root()

_EMOTIONS_PATH = PROJECT_ROOT / "resources" / "emotions.json"

_alias_map: dict[str, str] | None = None


def load_emotions() -> list[dict]:
    """读取全局 emotions 配置。[{"key": "happy", "name": "开心", "lang": [...]}, ...]"""
    with open(_EMOTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_emotion(value: str) -> str:
    """将 alias 解析为标准 key。找不到则原样返回。"""
    global _alias_map
    if _alias_map is None:
        _alias_map = {}
        for e in load_emotions():
            for a in e.get("alias", []):
                _alias_map[a] = e["key"]
    return _alias_map.get(value, value)


_lang_map: dict[str, list[str]] | None = None


def emotion_supports_lang(emotion: str, lang: str) -> bool:
    """检查 emotion 是否支持指定语言（如 "en"、"zh"）。
    emotion 不在配置中视为不支持。"""
    global _lang_map
    if _lang_map is None:
        _lang_map = {}
        for e in load_emotions():
            _lang_map[e["key"]] = e.get("lang", [])
    return lang in _lang_map.get(emotion, [])
