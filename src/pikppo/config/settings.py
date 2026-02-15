import os
from pathlib import Path
from dataclasses import dataclass, field

# 全局变量：存储 .env 文件所在目录（用于解析相对路径）
_env_file_dir: Path | None = None


def load_env_file(env_path: str | Path | None = None) -> None:
    """
    加载项目级 .env 文件（显式加载，不污染全局环境）。
    
    如果 env_path 为 None，自动查找项目根目录的 .env 文件。
    
    Args:
        env_path: .env 文件路径（None = 自动查找）
    """
    global _env_file_dir

    try:
        from dotenv import load_dotenv
    except ImportError:
        # dotenv 未安装，跳过
        return

    if env_path is None:
        # 自动查找：从当前文件向上查找，找到包含 .env 的目录
        current = Path(__file__).resolve()
        # 从 config/ 向上到项目根目录
        for parent in current.parents:
            env_file = parent / ".env"
            if env_file.exists():
                load_dotenv(env_file, override=False)  # override=False: 不覆盖已存在的环境变量
                _env_file_dir = env_file.parent  # 保存 .env 文件所在目录
                return
    else:
        env_path = Path(env_path)
        if env_path.exists():
            load_dotenv(env_path, override=False)
            _env_file_dir = env_path.parent  # 保存 .env 文件所在目录


def resolve_relative_path(path: str | Path) -> Path:
    """
    解析相对路径：如果是相对路径，则相对于 .env 文件所在目录。
    如果是绝对路径，直接返回。
    
    Args:
        path: 路径字符串或 Path 对象
        
    Returns:
        解析后的绝对路径
    """
    path = Path(path)

    # 如果是绝对路径，直接返回
    if path.is_absolute():
        return path

    # 如果是相对路径，相对于 .env 文件所在目录
    global _env_file_dir
    if _env_file_dir:
        return (_env_file_dir / path).resolve()

    # 如果找不到 .env 目录，相对于当前工作目录（向后兼容）
    return Path(path).resolve()


def get_openai_key() -> str | None:
    """
    仅从系统环境变量读取。
    优先使用 OPENAI_KEY，回退到官方 OPENAI_API_KEY。
    """
    return os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")


def get_gemini_key() -> str | None:
    """
    仅从系统环境变量读取。
    优先使用官方 GEMINI_API_KEY，回退到 GEMINI_KEY。
    """
    return os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEY")


def get_google_credentials() -> str | None:
    """
    从环境变量读取 Google Cloud 凭证文件路径。
    
    优先级：
    1. GCP_SPEECH_CREDENTIALS (项目级，推荐)
    2. GOOGLE_APPLICATION_CREDENTIALS (兼容全局)
    
    注意：应该通过 .env 文件设置 GCP_SPEECH_CREDENTIALS，而不是全局环境变量。
    """
    # 优先使用项目级环境变量（通过 .env 设置）
    creds = os.getenv("GCP_SPEECH_CREDENTIALS")
    if creds:
        return creds
    # 回退到全局环境变量（向后兼容）
    return os.getenv("GOOGLE_APPLICATION_CREDENTIALS")


def get_azure_speech_key() -> str | None:
    """
    从系统环境变量读取 Azure Speech Service 密钥。
    环境变量：AZURE_SPEECH_KEY
    """
    return os.getenv("AZURE_SPEECH_KEY")


def get_azure_speech_region() -> str | None:
    """
    从系统环境变量读取 Azure Speech Service 区域。
    环境变量：AZURE_SPEECH_REGION
    """
    return os.getenv("AZURE_SPEECH_REGION")


def get_openai_api_key() -> str | None:
    """
    从系统环境变量读取 OpenAI API Key。
    环境变量：OPENAI_API_KEY
    """
    return os.getenv("OPENAI_API_KEY")


@dataclass
class PipelineConfig:
    language: str = "zh"
    gemini_model: str = "gemini-2.0-flash"

    # 音频处理
    enhance_audio_for_asr: bool = True  # 应用音频增强（推荐用于有背景音乐的短剧）
    audio_enhance_level: str = "light"  # 音频增强级别：none（无）、light（轻量，默认）、heavy（重度，BGM 特别大时用）

    # 术语纠错
    term_corrections: dict[str, str] | None = None  # 术语纠错映射：{"天哥": "平安哥"}
    # 用于 ASS PlayRes 的视频分辨率；不改变实际视频
    ass_res_x: int = 720
    ass_res_y: int = 1280
    ass_margin_v: int = 60
    ass_font: str = "Arial"
    ass_font_size: int = 42
    # 翻译设置
    translator_list: list[str] = None  # 按顺序尝试的翻译器列表（默认：["gemini", "noop"]）
    translate_chunk_size: int = 50  # 批量翻译的块大小
    # TTS 设置
    tts_engine: str = "volcengine"  # TTS 引擎选择："azure" 或 "volcengine"（火山引擎，默认）
    tts_target_lufs: float = -16.0  # TTS 响度归一化目标（-16 LUFS 适合 TikTok/短剧，-14 适合 YouTube）
    tts_mix_mode: str = "ducking"  # 混音模式：ducking（侧链压缩，推荐）或 simple（简单混合）
    tts_scene_type: str = "quiet"  # 场景类型：quiet（安静，更激进，默认）、normal（正常）、intense（激烈）
    tts_volume: float = 1.4  # TTS 音频音量（默认 1.4，稍微放大确保清晰）
    # 默认：保留背景音乐 + 英文译音 + 原声气息（原声人声做强烈 ducking，而不是完全静音）
    # 说明：tts_mute_original 仍保留给旧的 synthesize_subtitle_to_audio 用，但在新 pipeline 的 mix 阶段默认不使用。
    tts_mute_original: bool = False
    tts_max_workers: int = 4  # TTS 并发 worker 数量（默认 4，可根据网络/配额调整）

    # 豆包大模型 ASR 配置（当前使用的 ASR 引擎）
    doubao_asr_preset: str = "asr_spk_semantic"
    doubao_postprofile: str = "axis"  # 后处理策略："axis"（默认）、"axis_default"、"axis_soft"
    doubao_audio_url: str | None = None  # 音频文件的公开访问 URL（如果为 None，则自动上传到 TOS）
    doubao_hotwords: list[str] = field(default_factory=lambda: [
        "平安",
        "平安哥",
        "二筒",
        "二饼",
        "四饼",
        "六条",
        "八条",
        "九万",
        "一万",
        "截胡",
        "放炮",
        "地胡",
        "屁胡",
        "一炮双响"
    ])  # ASR 热词列表（用于提高特定词汇识别准确率）

    # 强制重新运行（忽略缓存）
    force_asr: bool = False  # 强制重新运行 ASR（忽略缓存）

    # 旧版 ASR 配置（仅用于 dub_pipeline，保留以兼容）
    asr_use_vocals: bool = False  # 默认使用 raw 识别（vocals 作为备选，避免伪影干扰）
    google_stt_credentials_path: str | None = None  # Google STT 凭证路径（仅用于 dub_pipeline）
    whisper_model: str = "medium"  # Whisper 模型大小（仅用于 extract_zh.py）

    def __post_init__(self):
        """如果未提供，设置默认翻译器列表。"""
        if self.translator_list is None:
            self.translator_list = ["gemini", "noop"]
        if self.term_corrections is None:
            self.term_corrections = {}
        # 从环境变量读取 Azure 配置
        if self.azure_tts_region is None:
            self.azure_tts_region = get_azure_speech_region()
        if self.azure_tts_key is None:
            self.azure_tts_key = get_azure_speech_key()

    # OpenAI 翻译配置
    openai_model: str = "gpt-4o-mini"  # OpenAI 模型（推荐 gpt-4o-mini 性价比高）
    openai_temperature: float = 0.3  # 翻译温度（较低保证一致性）

    # Azure TTS 配置
    azure_tts_region: str | None = None  # Azure 区域（从环境变量读取）
    azure_tts_key: str | None = None  # Azure 密钥（从环境变量读取）
    azure_tts_language: str = "en-US"  # TTS 语言
    azure_tts_rate: float = 1.0  # 语速（1.0 = 正常，可调 0.8-1.2）
    azure_tts_pitch: float = 0  # 音调（-50 到 +50，0 = 正常）

    # Demucs 配置
    demucs_model: str = "htdemucs"  # Demucs 模型（htdemucs 或 htdemucs_ft）
    demucs_device: str = "cpu"  # 设备（cpu/cuda/mps）
    demucs_shifts: int = 1  # 移位次数（1=快，5=更准确但慢）
    demucs_split: bool = True  # 是否分割长音频

    # 配音流程配置
    dub_output_dir: str = "runs"  # 输出目录（runs/<video_id>/）
    dub_enable_qc: bool = True  # 启用 QC 报告
    dub_max_duration_stretch: float = 1.25  # 最大时长拉伸比例（超过则重译）
    dub_target_lufs: float = -16.0  # 目标响度（-16 LUFS 适合短视频）
    dub_true_peak: float = -1.5  # True Peak 限制（dB）

    # 声线池配置
    voice_pool_path: str | None = None  # 声线池配置文件路径（None = 使用默认）

    # ============================================================
    # Utterance Normalization 配置（Visual SSOT 生成）
    # ============================================================
    # 核心理念：ASR raw utterances 不是 SSOT，需要基于 speech + silence
    # 重建视觉/听觉友好的 utterance 边界，作为后续所有阶段的唯一 SSOT。

    # 静音切分阈值（ms）：两个发声段之间的静音超过此值则切分 utterance
    # - 小：过度切分，utterance 太碎
    # - 大：不同句子被合并
    utt_norm_silence_split_threshold_ms: int = 450  # 范围: 300-600

    # 最小 utterance 时长（ms）：避免过短的 utterance
    # - 小：可能出现单词级碎片
    # - 大：可能强制合并不该合并的内容
    utt_norm_min_duration_ms: int = 900  # 范围: 500-1500

    # 最大 utterance 时长（ms）：避免过长的 utterance
    # - 小：强制切分完整长句
    # - 大：字幕太长不易阅读
    utt_norm_max_duration_ms: int = 8000  # 范围: 5000-12000

    # 尾部静音上限（ms）：utterance end 最多包含多少静音
    # - 小：结束太突兀
    # - 大：吃掉太多静音影响节奏
    utt_norm_trailing_silence_cap_ms: int = 350  # 范围: 200-500

    # 是否保留 gap 为独立字段（推荐 True）
    # - True：保留原始信息，后续阶段可灵活使用 gap_after_ms
    # - False：gap 被吞进 end_ms，简化但丢失信息
    utt_norm_keep_gap_as_field: bool = True
