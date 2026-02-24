"""
时间感知的字幕级 MT 实现

职责：
- 实现 cue-level 的时间感知翻译
- 使用受限翻译 + 程序校验 + 二次压缩策略
- 返回带 metrics 的翻译结果
- 支持 Gemini（主引擎）+ OpenAI（fallback）

架构原则：
- 每条 cue 独立翻译（带时间约束）
- 不依赖批量上下文（可选，用于一致性）
- 直接写回 Subtitle Model 的 target 字段
"""
from typing import Any, Callable, Dict, List, Optional

from dubora.models.openai.translate_client import create_openai_client, call_openai_with_retry
from dubora.prompts import load_shared
from dubora.utils.logger import info, warning, error
from .time_aware_translate import (
    translate_cues_time_aware,
    calculate_max_chars,
)


def create_translate_fn(
    api_key: Optional[str] = None,
    model: str = "gemini-1.5-pro",
    temperature: float = 0.4,
    fallback_enabled: bool = False,
    fallback_api_key: Optional[str] = None,
    fallback_model: Optional[str] = "gpt-4o-mini",
) -> Callable[[str], str]:
    """
    创建翻译函数（单引擎 + 可选 fallback）。
    
    策略：
    - 默认：单引擎 + 同引擎重试（推荐，保证一致性）
    - 可选：启用 fallback（仅在高 SLA 需求时使用）
    
    Args:
        api_key: 主引擎 API key（Gemini 或 OpenAI）
        model: 主引擎模型名称（默认 gemini-1.5-pro）
        temperature: 温度参数（Gemini 推荐 0.3-0.5，OpenAI 推荐 0.3）
        fallback_enabled: 是否启用 fallback（默认 False，推荐保持关闭）
        fallback_api_key: Fallback API key（仅在 fallback_enabled=True 时使用）
        fallback_model: Fallback 模型名称（默认 gpt-4o-mini）
    
    Returns:
        翻译函数：prompt -> translation_text
    """
    # 判断主引擎类型
    is_gemini = model.startswith("gemini")
    
    # 创建主引擎
    primary_fn: Optional[Callable[[str], str]] = None
    if is_gemini:
        try:
            from dubora.models.gemini.translate_client import create_gemini_translate_fn
            primary_fn = create_gemini_translate_fn(
                api_key=api_key,
                model_name=model,
                temperature=temperature,
            )
            if primary_fn:
                info(f"Translation engine: Gemini ({model})")
            else:
                error("Failed to create Gemini client (returned None)")
                raise RuntimeError("Failed to create Gemini client: create_gemini_translate_fn returned None")
        except ImportError as e:
            error(f"Gemini module not available: {e}")
            error("Install with: pip install google-generativeai")
            error("Official package: https://pypi.org/project/google-generativeai/")
            raise RuntimeError(f"Gemini module not available: {e}") from e
        except Exception as e:
            error(f"Failed to initialize Gemini: {e}")
            raise RuntimeError(f"Failed to initialize Gemini: {e}") from e
    else:
        # OpenAI 主引擎
        try:
            if not api_key:
                from dubora.config.settings import get_openai_key
                api_key = get_openai_key()
            if not api_key:
                raise RuntimeError("OpenAI API key not found")
            
            client = create_openai_client(api_key)
            
            def openai_fn(prompt: str) -> str:
                # OpenAI prompt 格式：system 和 user 部分用双换行分隔
                if "\n\n" in prompt:
                    parts = prompt.split("\n\n", 1)
                    system_content = parts[0]
                    user_content = parts[1] if len(parts) > 1 else ""
                else:
                    system_content = load_shared("openai_default_system")
                    user_content = prompt
                
                messages = [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ]
                response_text = call_openai_with_retry(
                    client=client,
                    model=model,
                    messages=messages,
                    temperature=temperature,
                )
                return response_text.strip()
            
            primary_fn = openai_fn
            info(f"Translation engine: OpenAI ({model})")
        except Exception as e:
            error(f"Failed to initialize OpenAI: {e}")
            raise
    
    # 创建 fallback 引擎（仅在明确启用时）
    fallback_fn: Optional[Callable[[str], str]] = None
    if fallback_enabled:
        try:
            if not fallback_api_key:
                from dubora.config.settings import get_openai_key
                fallback_api_key = get_openai_key()
            if not fallback_api_key:
                warning("Fallback enabled but API key not found, fallback will be unavailable")
            else:
                client = create_openai_client(fallback_api_key)
                
                def openai_fallback_fn(prompt: str) -> str:
                    if "\n\n" in prompt:
                        parts = prompt.split("\n\n", 1)
                        system_content = parts[0]
                        user_content = parts[1] if len(parts) > 1 else ""
                    else:
                        system_content = load_shared("openai_default_system")
                        user_content = prompt
                    
                    messages = [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": user_content},
                    ]
                    response_text = call_openai_with_retry(
                        client=client,
                        model=fallback_model,
                        messages=messages,
                        temperature=0.3,
                    )
                    return response_text.strip()
                
                fallback_fn = openai_fallback_fn
                info(f"Fallback engine enabled: OpenAI ({fallback_model})")
        except Exception as e:
            warning(f"Failed to initialize fallback: {e}, continuing without fallback")
    
    # 创建翻译函数（单引擎 + 可选 fallback）
    def translate_fn(prompt: str) -> str:
        """
        翻译函数：接受 prompt，返回翻译结果。
        
        策略：
        - 优先使用主引擎
        - 如果失败且启用了 fallback，尝试 fallback
        - 否则抛出异常（由上层重试机制处理）
        
        Args:
            prompt: 翻译 prompt（包含约束和原文）
        
        Returns:
            翻译结果文本（已清理）
        
        Raises:
            RuntimeError: 如果所有可用引擎都失败
        """
        # 尝试主引擎
        if primary_fn:
            try:
                result = primary_fn(prompt)
                if result:
                    return result
                # 空结果：抛出异常，由上层重试机制处理
                raise RuntimeError("Primary engine returned empty result")
            except Exception as e:
                # 如果启用了 fallback，尝试 fallback
                if fallback_enabled and fallback_fn:
                    warning(f"Primary engine failed: {e}, trying fallback...")
                    try:
                        result = fallback_fn(prompt)
                        if result:
                            warning("Used fallback engine for this translation")
                            return result
                        raise RuntimeError("Fallback engine returned empty result")
                    except Exception as fallback_error:
                        error(f"Fallback engine also failed: {fallback_error}")
                        raise RuntimeError(f"All translation engines failed: primary={e}, fallback={fallback_error}")
                else:
                    # 未启用 fallback，直接抛出异常（由上层重试机制处理）
                    raise
    
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
