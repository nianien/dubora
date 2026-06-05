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


def _corpus(
    hotwords: Optional[List[str]] = None,
    scene_description: Optional[str] = None,
) -> CorpusConfig:
    """创建语料库配置。

    优先级：
    - 有 scene_description → dialog_ctx 格式（业务场景，对识别准确率提升显著）
    - 否则 hotwords → hotwords 格式
    - 都没 → 空 context
    """
    if scene_description:
        return CorpusConfig.from_scene(scene_description, hotwords=hotwords)
    return CorpusConfig.from_hotwords(hotwords if hotwords else [])


def _base_request_cfg(
    *,
    hotwords: Optional[List[str]] = None,
    scene_description: Optional[str] = None,
) -> RequestConfig:
    """
    统一基线：你不想每个 preset 都重复写的默认项都在这里。
    """
    return RequestConfig(
        model_name="bigmodel",  # 固定使用豆包 ASR 大模型
        enable_itn=False,  # 数字/金额规范化（不影响切分）
        enable_punc=True,  # 自动标点（只影响文本）
        enable_ddc=False,  # 语义顺滑会吞短句，必须关
        enable_speaker_info=True,  # 启用说话人分离
        enable_channel_split=False,  # 非物理双声道不要开
        show_utterances=True,  # 输出时间轴/分句/词（核心）
        enable_gender_detection=True,  # 性别信息（辅助 speaker）
        enable_emotion_detection=True,  # 情绪信息（辅助判断）
        enable_lid=True,  # 启用语种识别，则会在 additions 使用 lid_lang 标记，包含唱歌识别
        corpus=_corpus(hotwords=hotwords, scene_description=scene_description)
    )


def asr_vad_spk(
    *, hotwords: Optional[List[str]] = None, scene_description: Optional[str] = None,
) -> RequestConfig:
    """asr_vad_spk 预设（生产基线）：VAD 分句，end_window_size=800ms。"""
    base = _base_request_cfg(hotwords=hotwords, scene_description=scene_description)
    return replace(base, vad_segment=True, end_window_size=800)


def asr_vad_spk_smooth(
    *, hotwords: Optional[List[str]] = None, scene_description: Optional[str] = None,
) -> RequestConfig:
    """asr_vad_spk_smooth 预设（稳态参考）：VAD 分句，end_window_size=1000ms。"""
    base = _base_request_cfg(hotwords=hotwords, scene_description=scene_description)
    return replace(base, vad_segment=True, end_window_size=1000)


def asr_spk_semantic(
    *, hotwords: Optional[List[str]] = None, scene_description: Optional[str] = None,
) -> RequestConfig:
    """asr_spk_semantic 预设：不走 VAD，让模型语义切分。"""
    base = _base_request_cfg(hotwords=hotwords, scene_description=scene_description)
    return replace(base, vad_segment=False, end_window_size=None)


PRESETS: Dict[str, Callable[..., RequestConfig]] = {
    "asr_vad_spk": asr_vad_spk,
    "asr_vad_spk_smooth": asr_vad_spk_smooth,
    "asr_spk_semantic": asr_spk_semantic,
}


def get_preset(
    name: str,
    *,
    hotwords: Optional[List[str]] = None,
    scene_description: Optional[str] = None,
) -> RequestConfig:
    """获取预设配置。

    Args:
        name: 预设名称
        hotwords: 可选的热词列表
        scene_description: 可选的业务场景描述（dialog_ctx），
            优先于 hotwords —— 对识别准确率提升显著

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
    return factory(hotwords=hotwords, scene_description=scene_description)


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
