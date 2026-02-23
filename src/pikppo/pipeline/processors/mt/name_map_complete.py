"""
NameMap 补全器：使用 LLM 翻译缺失的人名（极简版）

职责：
- 只翻译第一次出现的名字
- 极简 Prompt：只做一件事
- 支持 Gemini 和 OpenAI 模型（统一使用同一模型）
"""
import json
from typing import Dict, List, Optional, Any, Callable

from pikppo.prompts import load_prompt
from pikppo.utils.logger import info, warning


def build_name_translation_prompt(
    missing_names: List[str],
) -> str:
    """
    构建人名翻译 Prompt（极简版）。从 YAML 模板加载。

    Args:
        missing_names: 需要翻译的人名列表

    Returns:
        Prompt 文本
    """
    names_text = "\n".join(f"- {name}" for name in missing_names)

    p = load_prompt("mt_name_translate",
        names_text=names_text,
    )
    return p.text


def complete_names_with_llm(
    missing_names: List[str],
    translate_fn: Callable[[str], str],
    is_gemini: bool = False,
) -> Dict[str, Dict[str, str]]:
    """
    使用 LLM 补全缺失的人名翻译（极简版）。
    
    统一使用与翻译相同的模型（Gemini 或 OpenAI），保持一致性。
    
    Args:
        missing_names: 需要翻译的人名列表
        translate_fn: 翻译函数（与主翻译使用相同的函数）
        is_gemini: 是否为 Gemini 模型（用于格式化 prompt）
    
    Returns:
        {src_name: {target, style}} 映射
    """
    if not missing_names:
        return {}
    
    prompt = build_name_translation_prompt(missing_names=missing_names)
    
    # 根据模型类型格式化 prompt
    if is_gemini:
        # Gemini 使用单字符串 prompt
        full_prompt = prompt
    else:
        # OpenAI 的 translate_fn 会自动处理 \n\n 分隔的 system/user 格式
        p = load_prompt("mt_name_translate")
        system_content = p.system if p.system else "You are a professional translator specializing in Chinese name translation. Always output valid JSON only."
        full_prompt = f"{system_content}\n\n{prompt}"
    
    try:
        # 使用统一的翻译函数
        result_text = translate_fn(full_prompt)
        
        # 尝试解析 JSON（可能包含额外的文本）
        result_text = result_text.strip()
        
        # 尝试提取 JSON 部分（如果响应包含其他文本）
        json_start = result_text.find('{')
        json_end = result_text.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            result_text = result_text[json_start:json_end]
        
        result_dict = json.loads(result_text)
        
        # 验证结果格式并推断 style
        validated = {}
        for src_name in missing_names:
            if src_name in result_dict:
                target = result_dict[src_name]
                # 推断 style（简单规则）
                if target.startswith("Mr. ") or target.startswith("Ms. "):
                    style = "honorific+surname"
                elif " " in target:
                    style = "pinyin"
                else:
                    style = "given-name"
                
                validated[src_name] = {
                    "target": target,
                    "style": style,
                }
            else:
                warning(f"LLM 未返回人名翻译: {src_name}")
                # 使用默认值（保留原文）
                validated[src_name] = {
                    "target": src_name,
                    "style": "keep",
                }
        
        return validated
    
    except Exception as e:
        warning(f"LLM 补全人名失败: {e}")
        # 返回空结果
        return {}
