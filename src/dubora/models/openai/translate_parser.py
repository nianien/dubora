"""
OpenAI 翻译输出解析：强对齐（使用 tag 对齐）
"""
import re
from typing import List, Optional

from dubora.utils.logger import warning


def parse_tagged_translation(
    response_text: str,
    expected_count: int,
) -> List[str]:
    """
    解析带 tag 的翻译输出（强对齐）。
    
    格式：
    <<<0>>>
    I did ten years in prison.
    <<<1>>>
    I was framed for killing my parents.
    
    Args:
        response_text: OpenAI 返回的文本
        expected_count: 期望的翻译数量
    
    Returns:
        翻译文本列表（长度必须等于 expected_count）
    """
    # 使用正则表达式提取所有 tag 和对应的内容
    pattern = r'<<<(\d+)>>>\s*\n(.*?)(?=\n<<<\d+>>>|$)'
    matches = re.findall(pattern, response_text, re.DOTALL | re.MULTILINE)
    
    # 构建字典：index -> translation
    translations_dict = {}
    for tag_str, content in matches:
        try:
            index = int(tag_str)
            translation = content.strip()
            translations_dict[index] = translation
        except ValueError:
            continue
    
    # 按索引顺序构建列表
    result = []
    for i in range(expected_count):
        if i in translations_dict:
            result.append(translations_dict[i])
        else:
            # 如果某个索引缺失，使用空字符串
            warning(f"Translation tag <<<{i}>>> not found in response, using empty string")
            result.append("")
    
    # 如果解析出的数量不足，用空字符串填充
    while len(result) < expected_count:
        warning(f"Expected {expected_count} translations but got {len(result)}, padding with empty strings")
        result.append("")
    
    # 如果解析出的数量超过预期，截断
    if len(result) > expected_count:
        warning(f"Expected {expected_count} translations but got {len(result)}, truncating")
        result = result[:expected_count]
    
    return result


def parse_simple_translation(
    response_text: str,
    expected_count: int,
) -> List[str]:
    """
    解析简单翻译输出（按行对齐，fallback 方案）。
    
    Args:
        response_text: OpenAI 返回的文本
        expected_count: 期望的翻译数量
    
    Returns:
        翻译文本列表（长度必须等于 expected_count）
    """
    lines = [line.strip() for line in response_text.split("\n") if line.strip()]
    
    # 移除可能的编号前缀
    cleaned_lines = []
    for line in lines:
        # 移除行首的数字和点号
        line = line.lstrip("0123456789. ").strip()
        if line:
            cleaned_lines.append(line)
    
    # 确保返回的行数与输入相同
    while len(cleaned_lines) < expected_count:
        cleaned_lines.append("")
    
    return cleaned_lines[:expected_count]
