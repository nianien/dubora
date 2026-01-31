"""
时间感知的字幕级 MT 实现

职责：
- 实现 cue-level 的时间感知翻译
- 使用受限翻译 + 程序校验 + 二次压缩策略
- 返回带 metrics 的翻译结果

架构原则：
- 每条 cue 独立翻译（带时间约束）
- 不依赖批量上下文（可选，用于一致性）
- 直接写回 Subtitle Model 的 target 字段
"""
from typing import Any, Callable, Dict, List

from pikppo.models.openai.translate_client import create_openai_client, call_openai_with_retry
from pikppo.utils.logger import info, warning
from .time_aware_translate import (
    translate_cues_time_aware,
    calculate_max_chars,
)


def create_translate_fn(
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
) -> Callable[[str], str]:
    """
    创建翻译函数（用于 time_aware_translate）。
    
    Args:
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
    
    Returns:
        翻译函数：prompt -> translation_text
    """
    client = create_openai_client(api_key)
    
    def translate_fn(prompt: str) -> str:
        """
        翻译函数：接受 prompt，返回翻译结果。
        
        Args:
            prompt: 翻译 prompt（包含约束和原文）
        
        Returns:
            翻译结果文本（已清理）
        """
        messages = [
            {"role": "system", "content": "You are a professional subtitle translator. Follow the constraints exactly."},
            {"role": "user", "content": prompt},
        ]
        
        try:
            response_text = call_openai_with_retry(
                client=client,
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return response_text.strip()
        except Exception as e:
            warning(f"Translation failed: {e}")
            return ""
    
    return translate_fn


def translate_cues_with_time_constraints(
    cues: List[Dict[str, Any]],
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    cps_limit: float = 15.0,
    max_retries: int = 2,
) -> List[Dict[str, Any]]:
    """
    翻译 cues（时间感知，cue-level）。
    
    Args:
        cues: Cue 列表，每个包含：
            - cue_id: 字幕单元 ID
            - start_ms: 开始时间（毫秒）
            - end_ms: 结束时间（毫秒）
            - source: {"lang": "zh", "text": "..."}
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
        cps_limit: CPS 限制（默认 15，推荐范围 12-17）
        max_retries: 最大重试次数（默认 2）
    
    Returns:
        翻译结果列表，每个包含：
        {
            "cue_id": "cue_0001",
            "text": "翻译文本",
            "max_chars": 19,
            "actual_chars": 18,
            "cps": 13.8,
            "status": "ok" | "compressed" | "truncated" | "failed" | "skipped",
            "retries": 0
        }
    """
    # 创建翻译函数
    translate_fn = create_translate_fn(
        api_key=api_key,
        model=model,
        temperature=temperature,
    )
    
    # 执行时间感知翻译
    info(f"Translating {len(cues)} cues with time constraints (CPS limit: {cps_limit})...")
    results = translate_cues_time_aware(
        cues=cues,
        translate_fn=translate_fn,
        cps_limit=cps_limit,
        max_retries=max_retries,
    )
    
    # 统计信息
    ok_count = sum(1 for r in results if r["status"] == "ok")
    compressed_count = sum(1 for r in results if r["status"] == "compressed")
    failed_count = sum(1 for r in results if r["status"] in ["failed", "truncated"])
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
    
    info(
        f"Translation completed: {ok_count} ok, {compressed_count} compressed, "
        f"{failed_count} failed/truncated, {skipped_count} skipped"
    )
    
    return results
