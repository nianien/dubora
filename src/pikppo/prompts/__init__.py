"""
Prompt 模板加载器

职责：
- 从 YAML 文件加载 prompt 模板
- 支持 $variable 变量替换（string.Template）
- 支持嵌套 section 访问（如 "mt_utterance_translate.retry_level_1"）
- 自动加载 mt_shared.yaml 中的共享片段

用法：
    from pikppo.prompts import load_prompt

    # 加载并渲染模板
    p = load_prompt("mt_utterance_translate",
        glossary_block="...",
        input_text="王哥钓二筒。",
    )
    print(p.system)  # 渲染后的 system prompt
    print(p.user)    # 渲染后的 user prompt

    # 访问嵌套 section
    p = load_prompt("mt_utterance_translate.retry_level_1",
        budget_sec="2.50",
        max_chars="35",
    )
    print(p.text)    # 渲染后的单段 prompt

    # 加载共享片段
    frag = load_shared("no_chinese_policy")
"""
import re
import string
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_PROMPTS_DIR = Path(__file__).parent
_cache: Dict[str, dict] = {}
_shared: Optional[dict] = None


def _load_yaml(name: str) -> dict:
    """加载并缓存 YAML 文件。"""
    if name not in _cache:
        yaml_path = _PROMPTS_DIR / f"{name}.yaml"
        with open(yaml_path, "r", encoding="utf-8") as f:
            _cache[name] = yaml.safe_load(f) or {}
    return _cache[name]


def _get_shared() -> dict:
    """加载共享片段（mt_shared.yaml）。"""
    global _shared
    if _shared is None:
        shared_path = _PROMPTS_DIR / "mt_shared.yaml"
        if shared_path.exists():
            with open(shared_path, "r", encoding="utf-8") as f:
                _shared = yaml.safe_load(f) or {}
        else:
            _shared = {}
    return _shared


def load_shared(name: str) -> str:
    """
    加载共享片段。

    Args:
        name: 片段名（mt_shared.yaml 中的 key）

    Returns:
        片段文本（找不到返回空字符串）
    """
    return _get_shared().get(name, "")


def _substitute(text: str, **kwargs: Any) -> str:
    """使用 string.Template 进行变量替换（$variable）。"""
    if not text:
        return ""
    return string.Template(text).safe_substitute(**kwargs)


class RenderedPrompt:
    """渲染后的 prompt，包含 system/user/text 字段。"""

    __slots__ = ("system", "user", "text")

    def __init__(self, system: str = "", user: str = "", text: str = ""):
        self.system = system.strip()
        self.user = user.strip()
        self.text = text.strip()

    def __repr__(self) -> str:
        parts = []
        if self.system:
            parts.append(f"system={len(self.system)} chars")
        if self.user:
            parts.append(f"user={len(self.user)} chars")
        if self.text:
            parts.append(f"text={len(self.text)} chars")
        return f"RenderedPrompt({', '.join(parts)})"


def load_prompt(name: str, **kwargs: Any) -> RenderedPrompt:
    """
    加载并渲染 prompt 模板。

    Args:
        name: 模板名，格式 "file_name" 或 "file_name.section.subsection"
              例: "mt_utterance_translate" 或 "mt_utterance_translate.retry_level_1"
        **kwargs: 模板变量（替换 $variable）

    Returns:
        RenderedPrompt，包含 system/user/text 字段
    """
    parts = name.split(".", 1)
    file_name = parts[0]
    section_path = parts[1] if len(parts) > 1 else None

    data = _load_yaml(file_name)

    # 导航到嵌套 section
    if section_path:
        for key in section_path.split("."):
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                raise KeyError(
                    f"Section '{section_path}' not found in template '{file_name}'"
                )

    if not isinstance(data, dict):
        # 如果 section 直接是字符串，作为 text 返回
        return RenderedPrompt(text=_substitute(str(data), **kwargs))

    system = _substitute(data.get("system", ""), **kwargs)
    user = _substitute(data.get("user", ""), **kwargs)
    text = _substitute(data.get("prompt", ""), **kwargs)

    return RenderedPrompt(system=system, user=user, text=text)


def clear_cache() -> None:
    """清除缓存（用于测试或热重载）。"""
    global _shared
    _cache.clear()
    _shared = None
