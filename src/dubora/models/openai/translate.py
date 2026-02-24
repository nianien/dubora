"""
OpenAI 翻译对外 API：build_translation_context(), translate_segments()
"""
import json
from typing import List, Dict, Any, Optional

from .translate_client import create_openai_client, call_openai_with_retry
from .translate_prompts import build_stage1_prompt, build_stage2_prompt, build_fallback_prompt
from .translate_parser import parse_tagged_translation, parse_simple_translation
from dubora.utils.logger import info, warning


def build_translation_context(
    zh_episode_text: str,
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    target_cps: str = "12-17",
    avoid_formal: bool = True,
    profanity_policy: str = "soften",
) -> Dict[str, Any]:
    """
    Stage 1: 生成翻译上下文。
    
    Args:
        zh_episode_text: 整集中文字幕文本（用换行符连接）
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
        max_chars_per_line: 每行最大字符数
        max_lines: 每段最大行数
        target_cps: 目标字符每秒
        avoid_formal: 避免正式用语
        profanity_policy: 脏话处理策略
    
    Returns:
        translation_context.json dict
    """
    client = create_openai_client(api_key)
    messages = build_stage1_prompt(
        zh_episode_text,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        target_cps=target_cps,
        avoid_formal=avoid_formal,
        profanity_policy=profanity_policy,
    )
    
    info(f"Calling OpenAI for translation context (model: {model})...")
    response_text = call_openai_with_retry(
        client=client,
        model=model,
        messages=messages,
        temperature=temperature,
    )
    
    # 尝试解析 JSON
    try:
        # 移除可能的 markdown 代码块标记
        response_text = response_text.strip()
        if response_text.startswith("```"):
            # 移除 ```json 或 ``` 标记
            lines = response_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            response_text = "\n".join(lines)
        
        context = json.loads(response_text)
    except json.JSONDecodeError as e:
        warning(f"Failed to parse context JSON, using default: {e}")
        # 返回默认上下文
        context = {
            "characters": [],
            "terminology": {},
            "style_notes": "Natural, conversational English subtitles",
            "tone": "conversational",
            "context": "",
        }
    
    # 添加元数据
    context["model"] = model
    context["temperature"] = temperature
    context["max_chars_per_line"] = max_chars_per_line
    context["max_lines"] = max_lines
    context["target_cps"] = target_cps
    
    return context


def translate_segments(
    segments: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    batch_size: int = 50,
    use_tag_alignment: bool = True,
) -> List[str]:
    """
    Stage 2: 翻译 segments（强对齐）。
    
    Args:
        segments: 中文 segments 列表（每个包含 text, start, end 等）
        context: Stage 1 生成的翻译上下文（可选）
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
        batch_size: 批处理大小
        use_tag_alignment: 是否使用 tag 对齐（推荐 True）
    
    Returns:
        翻译后的英文文本列表（长度必须等于 segments）
    """
    client = create_openai_client(api_key)
    
    # 过滤出有文本的 segments（保留索引映射）
    text_segments = []
    segment_indices = []  # 记录每个 text_segment 对应的原始索引
    
    for i, seg in enumerate(segments):
        text = seg.get("text", "").strip()
        if text:
            text_segments.append(seg)
            segment_indices.append(i)
    
    if not text_segments:
        # 如果所有 segments 都是空的，返回空字符串列表
        return [""] * len(segments)
    
    all_en_texts = []
    
    # 批量处理
    for batch_start in range(0, len(text_segments), batch_size):
        batch = text_segments[batch_start:batch_start + batch_size]
        batch_indices = segment_indices[batch_start:batch_start + batch_size]
        
        info(f"Translating batch {batch_start // batch_size + 1} ({len(batch)} segments)...")
        
        # 构建提示词
        if context:
            messages = build_stage2_prompt(
                batch,
                context,
                max_chars_per_line=context.get("max_chars_per_line", 42),
                max_lines=context.get("max_lines", 2),
            )
        else:
            # Fallback: 无上下文
            batch_texts = [seg.get("text", "") for seg in batch]
            messages = build_fallback_prompt(batch_texts)
        
        # 调用 API
        try:
            response_text = call_openai_with_retry(
                client=client,
                model=model,
                messages=messages,
                temperature=temperature,
            )
            
            # 解析响应
            if use_tag_alignment:
                batch_en_texts = parse_tagged_translation(response_text, len(batch))
            else:
                batch_en_texts = parse_simple_translation(response_text, len(batch))
            
            all_en_texts.extend(batch_en_texts)
            
        except Exception as e:
            warning(f"Translation failed for batch {batch_start // batch_size + 1}: {e}")
            # 使用空字符串填充失败的批次
            all_en_texts.extend([""] * len(batch))
    
    # 将翻译结果映射回原始 segments（包括空文本的 segments）
    en_texts = [""] * len(segments)
    for i, en_text in zip(segment_indices, all_en_texts):
        en_texts[i] = en_text
    
    return en_texts
