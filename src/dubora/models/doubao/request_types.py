"""
豆包 ASR API 请求类型定义

根据 API 文档生成的类型定义，用于构建请求参数。
包含校验、helper 方法等增强功能。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional
import json


# ----------------------------
# Enums
# ----------------------------

AudioFormat = Literal["raw", "wav", "mp3", "ogg", "m4a", "aac"]
AudioCodec = Literal["raw", "opus"]


# ----------------------------
# Helper functions
# ----------------------------

def _remove_none(obj: Any) -> Any:
    """递归移除 None；保留 False/0/""。"""
    if isinstance(obj, dict):
        return {k: _remove_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_remove_none(v) for v in obj if v is not None]
    return obj


# ----------------------------
# Level 2/3: Nested structures
# ----------------------------

@dataclass(frozen=True)
class UserInfo:
    """用户信息"""
    uid: Optional[str] = None


@dataclass(frozen=True)
class AudioConfig:
    """音频配置"""
    url: str
    format: AudioFormat
    language: Optional[str] = None
    codec: Optional[AudioCodec] = None
    rate: int = 16000
    bits: int = 16
    channel: int = 1

    def validate(self) -> None:
        """校验音频配置"""
        if self.channel not in (1, 2):
            raise ValueError("audio.channel must be 1 or 2")
        # rate 校验（可选，根据实际需求）
        # if self.rate not in (8000, 16000, 24000, 48000):
        #     raise ValueError("audio.rate must be one of: 8000, 16000, 24000, 48000")


@dataclass(frozen=True)
class CorpusConfig:
    """语料库配置"""
    boosting_table_name: Optional[str] = None
    correct_table_name: Optional[str] = None
    context: Optional[str] = None  # json-string

    @staticmethod
    def from_hotwords(hotwords: List[str]) -> "CorpusConfig":
        """
        从热词列表创建语料库配置。
        
        Args:
            hotwords: 热词列表
        
        Returns:
            CorpusConfig 实例
        """
        if hotwords:
            ctx = json.dumps(
                {"hotwords": [{"word": w} for w in hotwords]},
                ensure_ascii=False
            )
            return CorpusConfig(context=ctx)
        else:
            return CorpusConfig(context=None)


@dataclass(frozen=True)
class RequestConfig:
    """请求配置"""
    model_name: str = "bigmodel"
    
    # Model version
    ssd_version: Optional[str] = None
    model_version: Optional[str] = None
    
    # Text features
    enable_itn: bool = True
    enable_punc: bool = False
    enable_ddc: bool = False
    
    # Speaker / channels
    enable_speaker_info: bool = False
    enable_channel_split: bool = False
    
    # Output features
    show_utterances: bool = False
    show_speech_rate: bool = False
    show_volume: bool = False
    
    # Detection features
    enable_lid: bool = False
    enable_emotion_detection: bool = False
    enable_gender_detection: bool = False
    
    # VAD / segmentation
    vad_segment: bool = False
    end_window_size: Optional[int] = None
    
    # Filtering
    sensitive_words_filter: Optional[str] = None  # json-string
    
    # Feature classification
    enable_poi_fc: bool = False
    enable_music_fc: bool = False
    
    # Corpus
    corpus: Optional[CorpusConfig] = None

    def validate(self, audio: AudioConfig) -> None:
        """
        校验请求配置。
        
        Args:
            audio: 音频配置（用于交叉校验）
        
        Raises:
            ValueError: 如果配置无效
        """
        # VAD rules
        if self.vad_segment:
            if self.end_window_size is None:
                raise ValueError("vad_segment=True requires end_window_size")
            if not (300 <= int(self.end_window_size) <= 5000):
                raise ValueError("end_window_size must be in [300, 5000]")
        else:
            if self.end_window_size is not None:
                raise ValueError("vad_segment=False requires end_window_size=None")

        # Speaker rules
        if self.ssd_version is not None and not self.enable_speaker_info:
            raise ValueError("ssd_version is meaningless when enable_speaker_info=False")

        if self.enable_channel_split and audio.channel != 2:
            raise ValueError("enable_channel_split=True requires audio.channel=2")

        # doc constraint: ssd_version only effective for zh-CN or empty language
        if self.enable_speaker_info and self.ssd_version is not None:
            if audio.language not in (None, "", "zh-CN"):
                raise ValueError("ssd_version is effective only when audio.language is empty or zh-CN")


# ----------------------------
# Level 1: Main request
# ----------------------------

@dataclass(frozen=True)
class DoubaoASRRequest:
    """豆包 ASR API 请求"""
    audio: AudioConfig  # required
    request: RequestConfig  # required
    user: Optional[UserInfo] = None
    callback: Optional[str] = None
    callback_data: Optional[str] = None

    def validate(self) -> None:
        """校验整个请求"""
        self.audio.validate()
        self.request.validate(self.audio)

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典格式（用于 API 调用），自动过滤 None 值。
        
        Returns:
            字典格式的请求数据
        """
        self.validate()
        return _remove_none(asdict(self))
