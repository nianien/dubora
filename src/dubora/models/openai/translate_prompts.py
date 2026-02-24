"""
OpenAI 翻译提示词模板：Stage1/Stage2/Fallback
Prompt 内容从 YAML 模板加载（prompts/mt_*.yaml）
"""
from typing import List

from dubora.prompts import load_prompt


def build_stage1_prompt(
    zh_episode_text: str,
    *,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    target_cps: str = "12-17",
    avoid_formal: bool = True,
    profanity_policy: str = "soften",
) -> List[dict]:
    """
    构建 Stage 1 提示词（生成翻译上下文）。

    Args:
        zh_episode_text: 整集中文字幕文本（用换行符连接）
        max_chars_per_line: 每行最大字符数
        max_lines: 每段最大行数
        target_cps: 目标字符每秒
        avoid_formal: 避免正式用语
        profanity_policy: 脏话处理策略

    Returns:
        messages 列表（用于 OpenAI API）
    """
    p = load_prompt("mt_context_analysis",
        episode_text=zh_episode_text,
        max_chars_per_line=str(max_chars_per_line),
        max_lines=str(max_lines),
        target_cps=target_cps,
        avoid_formal=str(avoid_formal),
        profanity_policy=profanity_policy,
    )

    return [
        {"role": "system", "content": p.system},
        {"role": "user", "content": p.user},
    ]


def build_stage2_prompt(
    segments: List[dict],
    context: dict,
    *,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> List[dict]:
    """
    构建 Stage 2 提示词（翻译 segments）。

    Args:
        segments: 中文 segments 列表（每个包含 text, start, end 等）
        context: Stage 1 生成的翻译上下文
        max_chars_per_line: 每行最大字符数
        max_lines: 每段最大行数

    Returns:
        messages 列表（用于 OpenAI API）
    """
    # 构建 segments 文本（带索引）
    segments_text = "\n\n".join([
        f"Segment {i}:\n{seg.get('text', '')}"
        for i, seg in enumerate(segments)
        if seg.get('text', '').strip()
    ])

    context_str = (
        f"\nTranslation Context:\n"
        f"- Characters: {context.get('characters', [])}\n"
        f"- Terminology: {context.get('terminology', {})}\n"
        f"- Style: {context.get('style_notes', '')}\n"
        f"- Tone: {context.get('tone', 'conversational')}\n"
    )

    p = load_prompt("mt_segment_translate",
        context_str=context_str,
        segment_count=str(len(segments)),
        segments_text=segments_text,
        max_chars_per_line=str(max_chars_per_line),
        max_lines=str(max_lines),
    )

    return [
        {"role": "system", "content": p.system},
        {"role": "user", "content": p.user},
    ]


def build_fallback_prompt(
    texts: List[str],
) -> List[dict]:
    """
    构建 Fallback 提示词（简单翻译，无上下文）。

    Args:
        texts: 要翻译的文本列表

    Returns:
        messages 列表（用于 OpenAI API）
    """
    segments_text = "\n\n".join([
        f"<<<{i}>>>\n{text}"
        for i, text in enumerate(texts)
    ])

    p = load_prompt("mt_simple_translate",
        segment_count=str(len(texts)),
        segments_text=segments_text,
    )

    return [
        {"role": "system", "content": p.system},
        {"role": "user", "content": p.user},
    ]
