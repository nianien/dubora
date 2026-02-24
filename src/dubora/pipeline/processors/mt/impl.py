"""
MT Processor: 机器翻译业务逻辑层

职责：
- 封装翻译的完整流程（Stage 1: 上下文生成 + Stage 2: 翻译）
- 处理 segments 格式转换
- 不负责文件 IO（由 Phase 层负责）

架构原则：
- Processor 层：业务逻辑（翻译流程编排）
- Model 层：API 调用（OpenAI 接口）
"""
from typing import Dict, List, Any

from dubora.models.openai import build_translation_context, translate_segments
from dubora.utils.logger import info


def translate_episode_segments(
    segments: List[Dict[str, Any]],
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    target_cps: str = "12-17",
    avoid_formal: bool = True,
    profanity_policy: str = "soften",
) -> tuple[Dict[str, Any], List[str]]:
    """
    翻译整集 segments（Stage 1 + Stage 2）。
    
    Args:
        segments: 中文 segments 列表（每个包含 text, start, end, speaker 等）
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
        max_chars_per_line: 每行最大字符数
        max_lines: 每段最大行数
        target_cps: 目标字符每秒
        avoid_formal: 避免正式用语
        profanity_policy: 脏话处理策略
    
    Returns:
        (context, en_texts) 元组
        - context: 翻译上下文字典
        - en_texts: 翻译后的英文文本列表（与 segments 顺序对应）
    """
    # Stage 1: 生成翻译上下文
    info("Stage 1: Building translation context...")
    zh_episode_text = "\n".join([
        seg.get("text", "").strip()
        for seg in segments
        if seg.get("text", "").strip()
    ])
    
    if not zh_episode_text:
        raise ValueError("No text found in segments")
    
    context = build_translation_context(
        zh_episode_text=zh_episode_text,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        target_cps=target_cps,
        avoid_formal=avoid_formal,
        profanity_policy=profanity_policy,
    )
    
    # Stage 2: 翻译 segments
    info("Stage 2: Translating segments...")
    en_texts = translate_segments(
        segments=segments,
        context=context,
        api_key=api_key,
        model=model,
        temperature=temperature,
        use_tag_alignment=True,
    )
    
    return context, en_texts
