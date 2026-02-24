"""
OpenAI 翻译薄封装：只负责读写 + 调用 models.openai.translate
"""
from typing import List
from dubora.models.openai import build_translation_context, translate_segments


def translate_segments_openai(
    texts: List[str],
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
) -> List[str]:
    """
    使用 OpenAI API 翻译文本列表（向后兼容接口）。
    
    Args:
        texts: 要翻译的中文文本列表
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
    
    Returns:
        翻译后的英文文本列表
    """
    # 转换为 segments 格式
    segments = [
        {"text": text, "id": i}
        for i, text in enumerate(texts)
    ]
    
    # 调用新的 translate_segments API（无上下文，使用 fallback）
    en_texts = translate_segments(
        segments=segments,
        context=None,
        api_key=api_key,
        model=model,
        temperature=temperature,
        use_tag_alignment=True,
    )
    
    return en_texts
