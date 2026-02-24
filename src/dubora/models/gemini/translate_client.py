"""
Gemini 翻译客户端（用于字幕翻译）

使用新的 Google GenAI SDK (google-genai)
迁移自已废弃的 google-generativeai

严格按照官方文档实现，不猜测参数。
"""
import os
from typing import Callable, Optional, List

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

from dubora.utils.logger import info, warning, error


def get_gemini_api_key() -> Optional[str]:
    """
    获取 Gemini API key（从环境变量）。
    
    Returns:
        API key 或 None（如果未设置）
    """
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def list_available_models(client) -> List[str]:
    """
    列出可用的 Gemini 模型（支持 generateContent 的）。
    
    Args:
        client: genai.Client 实例
    
    Returns:
        可用模型名称列表
    """
    try:
        models = list(client.models.list())
        available = []
        for m in models:
            # 检查是否支持 generateContent
            if hasattr(m, 'supported_generation_methods') and 'generateContent' in m.supported_generation_methods:
                available.append(m.name)
            elif hasattr(m, 'name'):
                # 如果没有 supported_generation_methods 属性，也加入
                available.append(m.name)
        return available
    except Exception as e:
        warning(f"Failed to list models: {e}")
        return []


def find_available_model(client, preferred_models: List[str]) -> Optional[str]:
    """
    从可用模型列表中找到第一个可用的模型。
    
    Args:
        client: genai.Client 实例
        preferred_models: 优先尝试的模型名称列表
    
    Returns:
        可用的模型名称，或 None
    """
    available = list_available_models(client)
    if not available:
        return None
    
    # 先尝试优先列表中的模型
    for model_name in preferred_models:
        # 检查完整名称或短名称匹配
        for avail in available:
            if model_name in avail or avail.endswith(model_name):
                info(f"Found available model: {avail} (matched {model_name})")
                return avail
    
    # 如果没有匹配的，返回第一个可用的
    if available:
        info(f"Using first available model: {available[0]}")
        return available[0]
    
    return None


def create_gemini_client(api_key: Optional[str] = None, model_name: str = "gemini-2.0-flash"):
    """
    创建 Gemini 客户端（新 SDK）。
    
    严格按照官方文档：client = genai.Client(api_key=...)
    
    Args:
        api_key: Gemini API key（如果为 None，从环境变量读取）
        model_name: 首选模型名称（会验证是否可用）
    
    Returns:
        (client, actual_model_name) 元组，或 (None, None) 如果失败
    """
    if not GEMINI_AVAILABLE:
        error("google-genai package not installed.")
        error("Install with: pip install google-genai")
        error("Official package: https://pypi.org/project/google-genai/")
        error("Note: google-generativeai is deprecated, use google-genai instead")
        return None, None
    
    if api_key is None:
        api_key = get_gemini_api_key()
    
    if not api_key:
        error("GEMINI_API_KEY or GOOGLE_API_KEY environment variable not set")
        return None, None
    
    try:
        # 严格按照官方文档：client = genai.Client(api_key=api_key)
        client = genai.Client(api_key=api_key)
        
        # 列出可用模型并验证
        preferred_models = [
            model_name,
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-pro",
        ]
        
        actual_model = find_available_model(client, preferred_models)
        if not actual_model:
            error("No available Gemini models found. Check your API key and quota.")
            return None, None
        
        info(f"Gemini client created with model: {actual_model}")
        return client, actual_model
        
    except Exception as e:
        error(f"Failed to create Gemini client: {e}")
        return None, None


def call_gemini_with_retry(
    client,
    model_name: str,
    prompt: str,
    temperature: float = 0.3,
    max_retries: int = 3,
) -> Optional[str]:
    """
    调用 Gemini API（带重试）。
    
    严格按照官方示例：
    resp = client.models.generate_content(model="gemini-2.0-flash", contents="...")
    print(resp.text)
    
    只传 model 和 contents，不传其他参数。
    
    Args:
        client: genai.Client 实例
        model_name: 模型名称
        prompt: 提示文本
        temperature: 温度参数（新 SDK 可能不支持，暂时忽略）
        max_retries: 最大重试次数
    
    Returns:
        生成的文本或 None（如果失败）
    """
    if client is None:
        return None
    
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # 严格按照官方示例：只传 model 和 contents
            # resp = client.models.generate_content(model="gemini-2.0-flash", contents="...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            
            # 官方示例：response.text
            if response and hasattr(response, 'text') and response.text:
                return response.text.strip()
            
            warning(f"Gemini API returned empty response (attempt {attempt + 1}/{max_retries})")
        
        except Exception as e:
            error_detail = str(e)
            
            # 检查是否是 model not found 错误（不应该重试）
            if "not found" in error_detail.lower() or "not supported" in error_detail.lower():
                error(f"Model '{model_name}' not found or not supported: {error_detail}")
                error("This is a configuration error, not retrying.")
                return None
            
            last_error = error_detail
            if attempt < max_retries - 1:
                warning(f"Gemini API call failed (attempt {attempt + 1}/{max_retries}): {error_detail}")
            else:
                error(f"Gemini API call failed after {max_retries} attempts: {error_detail}")
    
    if last_error:
        error(f"Gemini API returned empty result after {max_retries} retries. Last error: {last_error}")
    
    return None


def create_gemini_translate_fn(
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.0-flash",
    temperature: float = 0.4,
) -> Optional[Callable[[str], str]]:
    """
    创建 Gemini 翻译函数（新 SDK）。
    
    Args:
        api_key: Gemini API key
        model_name: 模型名称（默认 gemini-2.0-flash）
        temperature: 温度参数（新 SDK 可能不支持，暂时忽略）
    
    Returns:
        翻译函数 (prompt -> text) 或 None（如果失败）
    """
    client, actual_model = create_gemini_client(api_key, model_name)
    if client is None or actual_model is None:
        return None
    
    def translate_fn(prompt: str) -> str:
        """
        Gemini 翻译函数。
        
        Args:
            prompt: 完整的 prompt 字符串（包含所有指令和内容）
        
        Returns:
            翻译结果文本
        
        Raises:
            RuntimeError: 如果翻译失败
        """
        result = call_gemini_with_retry(
            client=client,
            model_name=actual_model,
            prompt=prompt,
            temperature=temperature,
            max_retries=3,
        )
        if result is None:
            error_msg = f"Gemini translation failed: API returned empty result after 3 retries (model: {actual_model})"
            error(error_msg)
            raise RuntimeError(error_msg)
        if not result.strip():
            error("Gemini translation returned empty string")
            raise RuntimeError("Gemini translation failed: API returned empty string")
        return result
    
    return translate_fn
