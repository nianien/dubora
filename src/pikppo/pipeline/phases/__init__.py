"""
Pipeline phases registration.
"""
from .demux import DemuxPhase
from .asr import ASRPhase
from .srt import SubtitlePhase
from .mt import MTPhase
from .tts import TTSPhase
from .mix import MixPhase
from .burn import BurnPhase

# 顺序即依赖顺序（线性链）
ALL_PHASES = [
    DemuxPhase(),
    ASRPhase(),          # ASR 识别（只做识别，输出原始响应）
    SubtitlePhase(),     # 字幕后处理（从 ASR raw 生成字幕）
    MTPhase(),           # 机器翻译（依赖字幕）
    TTSPhase(),          # 语音合成
    MixPhase(),          # 混音
    BurnPhase(),         # 烧录字幕
]
