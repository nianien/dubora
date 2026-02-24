"""
ASR Processor: 语音识别（唯一对外入口）

职责：
- 接收 Phase 层的输入（audio_url）
- 调用内部实现进行 ASR 识别
- 返回 ProcessorResult（不负责文件 IO）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在 impl.py
- Phase 层只调用 processor.run()
"""
from typing import Any, Dict, List, Optional

from .._types import ProcessorResult
from .impl import transcribe
from dubora.schema import Utterance


def run(
    audio_url: str,
    *,
    preset: str = "asr_vad_spk",
    appid: Optional[str] = None,
    access_token: Optional[str] = None,
    hotwords: Optional[List[str]] = None,
    audio_format: Optional[str] = None,
    language: str = "zh-CN",
) -> ProcessorResult:
    """
    调用 ASR 服务，返回原始响应和 utterances。
    
    Args:
        audio_url: 音频文件 URL
        preset: ASR 预设名称（如 "asr_vad_spk"）
        appid: 应用标识（如果为 None，从环境变量读取）
        access_token: 访问令牌（如果为 None，从环境变量读取）
        hotwords: 热词列表（可选）
        audio_format: 音频格式（如果为 None，从 URL 猜测）
        language: 语言代码（默认：zh-CN）
    
    Returns:
        ProcessorResult:
        - data.raw_response: ASR API 原始响应（dict）
        - data.utterances: 解析后的 utterances 列表
        - meta: 元数据（utterances_count 等）
    """
    raw_response, utterances = transcribe(
        audio_url=audio_url,
        preset=preset,
        appid=appid,
        access_token=access_token,
        hotwords=hotwords,
        audio_format=audio_format,
        language=language,
    )
    
    return ProcessorResult(
        outputs=[],  # 由 Phase 声明 outputs，processor 只负责业务处理
        data={
            "raw_response": raw_response,
            "utterances": utterances,
        },
        metrics={
            "utterances_count": len(utterances),
            "preset": preset,
        },
    )
