import os
from functools import lru_cache
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
    解析相对路径：相对于"项目根"（含 .env 的目录），与运行进程的 cwd 无关。

    解析顺序：
      1. 绝对路径直接返回
      2. 已设 _env_file_dir → 相对于它
      3. 否则自动调一次 load_env_file()，从 settings.py 向上找 .env 设
      4. 极端情况找不到 .env → 从 settings.py 反推项目根（比 cwd 稳定）

    历史上这里曾 fallback 到 `Path(path).resolve()` 用 cwd 解析，
    导致 cwd 切到子目录时（如前端开发服务器 / IDE 测试运行器从 web/ 下跑
    Python）会创建错位的数据缓存目录（如 web/data/gcs/...）。已移除。
    """
    path = Path(path)
    if path.is_absolute():
        return path

    global _env_file_dir
    if _env_file_dir is None:
        load_env_file()  # 幂等：load_dotenv override=False，已加载 env 不受影响

    if _env_file_dir is not None:
        return (_env_file_dir / path).resolve()

    # 兜底：settings.py 在 packages/core/src/dubora_core/config/，向上 5 层到项目根
    return (Path(__file__).resolve().parents[5] / path).resolve()


@lru_cache(maxsize=1)
def get_data_root() -> Path:
    """DATA_DIR, default data/. Root for all data directories."""
    raw = os.getenv("DATA_DIR", "data")
    data_root = resolve_relative_path(raw)
    data_root.mkdir(parents=True, exist_ok=True)
    return data_root


def get_drama_dir(drama_name: str) -> Path:
    d = get_data_root() / "pipeline" / drama_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_database_url() -> str:
    """DB_URL env var. Default: postgresql://localhost:5432/dubora.

    生产连 Neon serverless Postgres（host ep-young-sea-a1b6u97h-pooler.
    ap-southeast-1.aws.neon.tech，sslmode=require）；本地开发可连任意 Postgres。
    早期曾用 SQLite 文件（data/db/dubora.db），2026-03 切换到 Postgres 后
    get_db_dir / get_db_path 已删除。
    """
    return os.getenv("DB_URL", "postgresql://localhost:5432/dubora")


def get_workdir(drama_name: str, episode_number: int) -> Path:
    return get_drama_dir(drama_name) / str(episode_number)



def get_voice_preview_cache_dir() -> Path:
    cache_dir = get_data_root() / ".cache" / "voice-preview"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_faststart_cache_dir() -> Path:
    cache_dir = get_data_root() / ".cache" / "faststart"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_openai_key() -> str | None:
    """
    仅从系统环境变量读取。
    优先使用 OPENAI_KEY，回退到官方 OPENAI_API_KEY。
    """
    return os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")


def get_gemini_key() -> str | None:
    """
    仅从系统环境变量读取。
    优先使用官方 GEMINI_API_KEY，回退到 GOOGLE_API_KEY。
    """
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


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
    # ── ASR 配置（豆包单源 + Gemini scene context 辅助）──
    asr_use_vocals: bool = False  # True = 用分离后的 vocals 做 ASR

    # ── MT 配置 ──
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.5-flash"))
    openai_model: str = "gpt-4o-mini"

    # ── TTS 配置 ──
    tts_engine: str = field(default_factory=lambda: os.getenv("TTS_ENGINE", "volcengine"))  # volcengine / fish
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
