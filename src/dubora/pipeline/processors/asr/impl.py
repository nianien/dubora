"""
ASR 转录：调用 ASR 服务，返回 utterances

职责：
- 输入：audio_url + preset
- 输出：utterances（带时间轴 / speaker）
- 不关心：srt、postprofile、文件系统
"""
import os
from typing import List, Optional, Tuple, Any, Dict

from dubora.models.doubao import (
    DoubaoASRClient,
    guess_audio_format,
    get_preset,
    parse_utterances,
)
from dubora.schema import Utterance
from dubora.models.doubao.request_types import DoubaoASRRequest, AudioConfig, UserInfo
from dubora.utils.logger import info


def transcribe(
        audio_url: str,
        preset: str,
        *,
        appid: Optional[str] = None,
        access_token: Optional[str] = None,
        hotwords: Optional[List[str]] = None,
        audio_format: Optional[str] = None,
        language: str = "zh-CN",
) -> Tuple[Dict[str, Any], List[Utterance]]:
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
        (raw_response, utterances)
        - raw_response: ASR API 原始响应（dict）
        - utterances: 解析后的 utterances 列表
    
    Raises:
        ValueError: 如果 appid 或 access_token 未设置
    """
    # 获取配置
    if appid is None:
        appid = os.getenv("DOUBAO_APPID")
    if access_token is None:
        access_token = os.getenv("DOUBAO_ACCESS_TOKEN")

    if not appid or not access_token:
        raise ValueError(
            "DOUBAO_APPID 和 DOUBAO_ACCESS_TOKEN 必须设置。"
            "请通过参数提供或设置环境变量"
        )

    # 创建客户端
    client = DoubaoASRClient(app_key=appid, access_key=access_token)

    # 1. 获取预设配置
    request_config = get_preset(preset, hotwords=hotwords)

    # 2. 猜测音频格式（如果未提供）
    if audio_format is None:
        audio_format = guess_audio_format(audio_url)

    # 3. 构建完整请求
    req = DoubaoASRRequest(
        user=UserInfo(uid=str(appid)),
        audio=AudioConfig(
            url=audio_url,
            format=audio_format,
            language=language,
            rate=16000,  # 固定 16kHz
            bits=16,  # 固定 16-bit
            channel=1,  # 固定单声道
        ),
        request=request_config,
    )

    # 4. 调用 API
    info(f"调用 ASR API (预设: {preset}, format: {audio_format})...")
    raw_response = client.submit_and_poll(
        req=req,
        resource_id="volc.seedasr.auc",
        poll_interval_s=2.0,
        max_wait_s=3600,
    )

    # 5. 解析结果
    utterances = parse_utterances(raw_response)

    return raw_response, utterances
