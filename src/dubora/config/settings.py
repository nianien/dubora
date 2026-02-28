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


@dataclass
class PipelineConfig:
    # ── ASR 配置 ──
    doubao_asr_preset: str = "asr_spk_semantic"
    doubao_audio_url: str | None = None  # 音频 URL（None = 自动上传 TOS）
    doubao_hotwords: list[str] = field(default_factory=lambda: ["平安", "平安哥", "于平安"])
    asr_use_vocals: bool = False  # True = 用分离后的 vocals 做 ASR

    # ── SUB 配置 ──
    doubao_postprofile: str = "axis"  # 后处理策略：axis / axis_default / axis_soft
    # Utterance Normalization（speech + silence 重建 utterance 边界）
    utt_norm_silence_split_threshold_ms: int = 450  # 静音切分阈值 (300-600)
    utt_norm_min_duration_ms: int = 900  # 最小 utterance 时长 (500-1500)
    utt_norm_max_duration_ms: int = 5000  # 最大 utterance 时长 (5000-12000)
    utt_norm_trailing_silence_cap_ms: int = 350  # 尾部静音上限 (200-500)
    utt_norm_keep_gap_as_field: bool = True  # 保留 gap 为独立字段

    # ── RESEG 配置 ──
    reseg_enabled: bool = True              # 是否启用 LLM 断句
    reseg_min_chars: int = 6                # 拆分后每段最少中文字数（防止碎片；原句超标时自动放宽到 3）
    reseg_max_chars_trigger: int = 25       # 触发拆分的字数阈值
    reseg_max_duration_trigger: int = 6000  # 触发拆分的时长阈值（ms）

    # ── MT 配置 ──
    gemini_model: str = "gemini-2.0-flash"
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.3

    # ── TTS 配置 ──
    tts_engine: str = "volcengine"  # volcengine / azure
    tts_max_workers: int = 4  # 并发 worker 数
    tts_mute_original: bool = False  # 静音原声（默认 ducking）
    tts_volume: float = 1.4  # TTS 音量
    voice_pool_path: str | None = None  # 声线池配置路径
    # Azure TTS
    azure_tts_region: str | None = None
    azure_tts_key: str | None = None
    azure_tts_language: str = "en-US"

    # ── MIX 配置 ──
    dub_target_lufs: float = -16.0  # 目标响度（-16 LUFS 适合短视频）
    dub_true_peak: float = -1.5  # True Peak 限制 (dB)

    def __post_init__(self):
        if self.azure_tts_region is None:
            self.azure_tts_region = get_azure_speech_region()
        if self.azure_tts_key is None:
            self.azure_tts_key = get_azure_speech_key()
