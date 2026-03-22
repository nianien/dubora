"""OpenAI Whisper ASR。

需要环境变量：OPENAI_API_KEY（或 OPENAI_KEY）
"""

from pathlib import Path

from dubora_core.utils.logger import info


def transcribe_openai(audio_path: Path) -> dict:
    """调用 OpenAI Whisper API。

    Args:
        audio_path: 本地音频文件路径

    Returns:
        原始响应 dict（含 segments + words）
    """
    from openai import OpenAI
    from dubora_core.config.settings import get_openai_key

    key = get_openai_key()
    if not key:
        raise RuntimeError("需要 OPENAI_API_KEY")

    client = OpenAI(api_key=key)

    info("OpenAI Whisper 提交任务...")
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )

    data = result.model_dump()
    segments = data.get("segments", [])
    info(f"OpenAI Whisper 完成: {len(segments)} segments")
    return data
