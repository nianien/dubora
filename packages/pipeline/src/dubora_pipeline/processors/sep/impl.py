"""
人声分离工具：使用 Demucs 分离人声和背景音乐（进程内调用，输出用 soundfile 写）

为什么不走 demucs 命令行：
demucs CLI 在保存 wav 时调用 torchaudio.save，而 torchaudio 2.9 的 save 只能用
torchcodec 编码（旧的 soundfile/sox backend 已移除）。torchcodec 在 macOS 上常
因找不到匹配版本的 ffmpeg 动态库（libavutil 等）而 dlopen 失败
（torchcodec issue #570）。这里直接在进程内调用 demucs 的 Python API 做分离，
再用 soundfile（wheel 自带 libsndfile，不依赖系统 ffmpeg）写 wav，彻底绕开
torchcodec。读取输入仍用 demucs 的 AudioFile（走 ffmpeg 命令行，该步本就正常）。
"""
import os
from functools import lru_cache
from pathlib import Path

from dubora_core.utils.logger import info


def _ensure_ssl_cert() -> None:
    """确保 torch.hub 首次下载模型权重时能验证 HTTPS 证书。

    macOS python.org 安装包若未运行 Install Certificates.command，解释器缺少
    CA 根证书，torch.hub 从 dl.fbaipublicfiles.com 下载会触发
    CERTIFICATE_VERIFY_FAILED。用 certifi 的证书包兜底。
    """
    if not os.environ.get("SSL_CERT_FILE"):
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()


@lru_cache(maxsize=2)
def _load_model(name: str):
    """加载并缓存 Demucs 模型（eval 只读，可跨 episode 复用）。

    Worker 是长驻进程、串行处理多集，缓存可省去每集重复反序列化模型的开销。
    """
    _ensure_ssl_cert()
    from demucs.pretrained import get_model

    model = get_model(name)
    model.cpu()
    model.eval()
    return model


def _write_wav(tensor, path: Path, samplerate: int) -> None:
    """把 [channels, time] 的 float32 张量用 soundfile 写成 16-bit PCM wav。"""
    import soundfile as sf
    from demucs.audio import prevent_clip

    data = prevent_clip(tensor, mode="rescale")
    # soundfile 期望 [frames, channels]
    data = data.t().contiguous().cpu().numpy()
    sf.write(str(path), data, samplerate, subtype="PCM_16")


def separate_vocals(input_path: str, output_dir: str, model: str = "htdemucs") -> tuple[str, str]:
    """
    使用 Demucs 分离人声和背景音乐（进程内）。

    Args:
        input_path: 输入音频文件路径（.wav）
        output_dir: 输出目录（将在其下创建 {model}/{stem}/vocals.wav 和 no_vocals.wav）
        model: Demucs 模型名称，默认 "htdemucs"（推荐 v4 系列）

    Returns:
        (vocals_path, accompaniment_path) 元组

    Raises:
        FileNotFoundError: 如果输入文件不存在
        RuntimeError: 如果分离失败
    """
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"Audio file not found: {input_path}")

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    # 输出结构：output_dir/model_name/input_filename/{vocals,no_vocals}.wav
    demucs_output_dir = output_dir_path / model / input_file.stem
    vocals_path = demucs_output_dir / "vocals.wav"
    accompaniment_path = demucs_output_dir / "no_vocals.wav"

    # 缓存命中直接返回
    if vocals_path.exists() and accompaniment_path.exists():
        info(f"Using cached separation results from {demucs_output_dir}")
        return str(vocals_path), str(accompaniment_path)

    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile

    info(f"Separating vocals from {input_file.name} using Demucs ({model}) [in-process]...")

    demucs_model = _load_model(model)

    source_names = list(demucs_model.sources)
    if "vocals" not in source_names:
        raise RuntimeError(
            f"Model {model} has no 'vocals' source (sources={source_names})"
        )

    # 读取输入（AudioFile 走 ffmpeg 命令行），得到 [channels, time]
    wav = AudioFile(input_file).read(
        streams=0,
        samplerate=demucs_model.samplerate,
        channels=demucs_model.audio_channels,
    )

    # 与 demucs.separate 一致的归一化
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / ref.std()

    with torch.no_grad():
        sources = apply_model(
            demucs_model,
            wav[None],
            device="cpu",
            shifts=1,
            split=True,
            overlap=0.25,
            progress=False,
        )[0]
    sources = sources * ref.std() + ref.mean()

    vocals_idx = source_names.index("vocals")
    vocals = sources[vocals_idx]
    others = [s for i, s in enumerate(sources) if i != vocals_idx]
    accompaniment = torch.stack(others).sum(0)

    demucs_output_dir.mkdir(parents=True, exist_ok=True)
    _write_wav(vocals, vocals_path, demucs_model.samplerate)
    _write_wav(accompaniment, accompaniment_path, demucs_model.samplerate)

    if not vocals_path.exists() or vocals_path.stat().st_size == 0:
        raise RuntimeError(f"Vocal separation failed: {vocals_path} missing or empty")
    if not accompaniment_path.exists() or accompaniment_path.stat().st_size == 0:
        raise RuntimeError(f"Vocal separation failed: {accompaniment_path} missing or empty")

    vocals_size = vocals_path.stat().st_size
    accompaniment_size = accompaniment_path.stat().st_size
    info("Vocal separation succeeded:")
    info(f"  Vocals: {vocals_path.name} (size: {vocals_size / 1024 / 1024:.2f} MB)")
    info(f"  Accompaniment: {accompaniment_path.name} (size: {accompaniment_size / 1024 / 1024:.2f} MB)")

    return str(vocals_path), str(accompaniment_path)
