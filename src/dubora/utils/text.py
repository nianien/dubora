"""
文本处理工具函数

职责：
- 文本规范化（空格、标点处理）
- 通用文本处理逻辑

这些函数是通用的，不绑定到特定的 provider 或模块。
"""


def normalize_text(t: str) -> str:
    """
    文本规范化：处理空格和标点。
    
    规则：
    - 保留标点符号
    - 规范化空格（全角空格转半角，合并多个空格）
    
    Args:
        t: 原始文本
    
    Returns:
        规范化后的文本
    """
    # Keep punctuation; just normalize whitespace.
    t = t.replace("\u3000", " ").strip()
    # collapse multiple spaces
    while "  " in t:
        t = t.replace("  ", " ")
    return t
