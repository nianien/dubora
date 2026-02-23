"""
豆包 ASR 预设配置（基于 request_types.RequestConfig）

职责：
- 提供预设的 RequestConfig 构造器/工厂函数
- 对 RequestConfig 的默认值/覆写


"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional

from .request_types import RequestConfig, CorpusConfig


def _corpus(hotwords: Optional[List[str]]) -> CorpusConfig:
    """
    创建语料库配置。
    
    Args:
        hotwords: 热词列表（如果为 None 或空列表，则不设置热词）
    """
    # 如果 hotwords 为 None 或空列表，使用空列表（不设置热词）
    # 热词的默认值应该在调用层（settings.py）设置
    return CorpusConfig.from_hotwords(hotwords if hotwords else [])


def _base_request_cfg(*, hotwords: Optional[List[str]] = None) -> RequestConfig:
    """
    统一基线：你不想每个 preset 都重复写的默认项都在这里。
    """
    return RequestConfig(
        model_name="bigmodel",  # 固定使用豆包 ASR 大模型
        enable_itn=False,  # 数字/金额规范化（不影响切分）
        enable_punc=True,  # 自动标点（只影响文本）
        enable_ddc=False,  # 语义顺滑会吞短句，必须关
        enable_speaker_info=True,  # 启用说话人分离
        # model_version="400",  # 已移除：API 返回 "invalid bigasr model version" 错误
        ssd_version="200",  # 中文 speaker 最稳定版本
        enable_channel_split=False,  # 非物理双声道不要开
        show_utterances=True,  # 输出时间轴/分句/词（核心）
        enable_gender_detection=True,  # 性别信息（辅助 speaker）
        enable_emotion_detection=True,  # 情绪信息（辅助判断）
        corpus=_corpus(hotwords),  # 热词/上下文（稳定人名称呼）
        # show_speech_rate=True,  # 分句信息携带语速
        # show_volume=True  # 分句信息携带音量
    )


def asr_vad_spk(*, hotwords: Optional[List[str]] = None) -> RequestConfig:
    """
    创建 asr_vad_spk 预设（生产基线）。
    
    特点：
    - VAD 分句，end_window_size=800ms（验证过稳定）
    - 开启 speaker 识别
    """
    base = _base_request_cfg(hotwords=hotwords)
    return replace(
        base,
        vad_segment=True,
        end_window_size=800,
    )


def asr_vad_spk_smooth(*, hotwords: Optional[List[str]] = None) -> RequestConfig:
    """
    创建 asr_vad_spk_smooth 预设（稳态参考）。
    
    特点：
    - VAD 分句，end_window_size=1000ms（更少碎片，但可能吞掉换人边界）
    - 开启 speaker 识别
    """
    base = _base_request_cfg(hotwords=hotwords)
    return replace(
        base,
        vad_segment=True,
        end_window_size=1000,
    )


def asr_spk_semantic(*, hotwords: Optional[List[str]] = None) -> RequestConfig:
    """
    创建 asr_spk_semantic 预设（语义对照）。
    
    特点：
    - 不走 VAD（完整性强，让模型语义切分）
    - 开启 speaker 识别
    """
    base = _base_request_cfg(hotwords=hotwords)
    return replace(
        base,
        vad_segment=False,
        # vad_segment=False时，end_window_size必须为None
        end_window_size=None,
    )


PRESETS: Dict[str, Callable[..., RequestConfig]] = {
    "asr_vad_spk": asr_vad_spk,
    "asr_vad_spk_smooth": asr_vad_spk_smooth,
    "asr_spk_semantic": asr_spk_semantic,
}


def get_preset(name: str, *, hotwords: Optional[List[str]] = None) -> RequestConfig:
    """
    获取预设配置。
    
    Args:
        name: 预设名称
        hotwords: 可选的热词列表（如果不提供，使用默认值）
    
    Returns:
        RequestConfig 实例
    
    Raises:
        KeyError: 如果预设不存在
    """
    if name not in PRESETS:
        raise KeyError(
            f"未知的预设名称: {name}\n"
            f"可用预设: {', '.join(sorted(PRESETS.keys()))}\n"
            f"推荐使用: asr_vad_spk（VAD + Speaker，默认）"
        )
    factory = PRESETS[name]
    return factory(hotwords=hotwords)


def get_presets() -> Dict[str, Dict[str, Any]]:
    """
    获取所有预设配置
    
    Returns:
        预设名称到配置字典的映射
    """
    from dataclasses import asdict
    from .request_types import _remove_none

    return {
        name: _remove_none(asdict(factory()))
        for name, factory in PRESETS.items()
    }
