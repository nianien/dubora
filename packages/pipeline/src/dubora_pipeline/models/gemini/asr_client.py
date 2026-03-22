"""
Gemini 多模态 ASR 客户端

使用 Gemini 的音频理解能力进行语音识别，支持 speaker diarization 和 emotion 标注。
"""
import json
from typing import List, Optional

from dubora_core.utils.logger import info
from dubora_pipeline.prompts import load_prompt


def transcribe_with_gemini(
    audio_url: str,
    *,
    api_key: Optional[str] = None,
    model_name: str,
) -> List[dict]:
    """使用 Gemini 多模态能力进行语音识别。

    Args:
        audio_url: 音频文件签名 URL（TOS/GCS 均可）
        api_key: Gemini API key
        model_name: Gemini 模型名称

    Returns:
        list[dict]，每个元素包含 speaker/start_ms/end_ms/text/emotion/gender

    Raises:
        RuntimeError: API key 缺失或 API 调用失败
    """
    from google import genai
    from google.genai import types

    if not api_key:
        raise RuntimeError("Gemini ASR 需要 api_key 参数")

    prompt = load_prompt("asr_gemini")

    client = genai.Client(api_key=api_key)

    info(f"Gemini ASR: calling {model_name}...")
    response = client.models.generate_content(
        model=model_name,
        contents=[
            types.Content(
                parts=[
                    types.Part.from_uri(
                        file_uri=audio_url,
                        mime_type="audio/wav",
                    ),
                    types.Part.from_text(text=prompt.user),
                ]
            )
        ],
        config=types.GenerateContentConfig(
            system_instruction=prompt.system,
            response_mime_type="application/json",
        ),
    )

    segments = json.loads(response.text)
    result = [
        {
            "speaker": str(seg.get("speaker", "0")),
            "start_ms": int(seg.get("start_ms", 0)),
            "end_ms": int(seg.get("end_ms", 0)),
            "text": seg.get("text", ""),
            "emotion": seg.get("emotion"),
            "gender": seg.get("gender"),
        }
        for seg in segments
        if seg.get("text", "").strip()
    ]

    info(f"Gemini ASR: {len(result)} segments")
    return result
