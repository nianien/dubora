"""
OpenAI 翻译客户端：SDK 初始化 + retry/backoff
"""
import time
from typing import Optional

try:
    import openai
    from openai import OpenAI
except ImportError:
    openai = None
    OpenAI = None

from dubora.utils.logger import error, warning


def create_openai_client(api_key: str) -> OpenAI:
    """创建 OpenAI 客户端。"""
    if openai is None:
        raise ImportError(
            "openai package is not installed. Please install it with: pip install openai"
        )
    
    return OpenAI(api_key=api_key)


def call_openai_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.3,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> str:
    """
    调用 OpenAI API，带重试机制。
    
    Args:
        client: OpenAI 客户端
        model: 模型名称
        messages: 消息列表
        temperature: 温度参数
        max_retries: 最大重试次数
        retry_delay: 重试延迟（秒）
    
    Returns:
        响应内容（字符串）
    
    Raises:
        RuntimeError: 如果所有重试都失败
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = retry_delay * (2 ** attempt)  # 指数退避
                warning(f"OpenAI API call failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                error(f"OpenAI API call failed after {max_retries} attempts: {e}")
    
    raise RuntimeError(f"OpenAI API call failed after {max_retries} attempts: {last_error}")
