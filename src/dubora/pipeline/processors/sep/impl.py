"""
人声分离工具：使用 Demucs 分离人声和背景音乐

职责：
- 使用 Demucs htdemucs 模型分离人声和背景
- 输出 vocals.wav 和 accompaniment.wav
"""
import os
import subprocess
from pathlib import Path
from dubora.utils.logger import info, error


def separate_vocals(input_path: str, output_dir: str, model: str = "htdemucs") -> tuple[str, str]:
    """
    使用 Demucs 分离人声和背景音乐。
    
    Args:
        input_path: 输入音频文件路径（.wav）
        output_dir: 输出目录（将在此目录下创建 vocals.wav 和 accompaniment.wav）
        model: Demucs 模型名称，默认 "htdemucs"（推荐 v4 系列）
    
    Returns:
        (vocals_path, accompaniment_path) 元组
    
    Raises:
        FileNotFoundError: 如果输入文件不存在
        RuntimeError: 如果 Demucs 分离失败
    """
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"Audio file not found: {input_path}")
    
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Demucs 输出结构：output_dir/model_name/input_filename/vocals.wav
    model_name = model
    input_stem = input_file.stem
    demucs_output_dir = output_dir_path / model_name / input_stem
    
    # 检查是否已经分离过（缓存）
    vocals_cached = demucs_output_dir / "vocals.wav"
    accompaniment_cached = demucs_output_dir / "accompaniment.wav"
    no_vocals_cached = demucs_output_dir / "no_vocals.wav"
    
    if vocals_cached.exists() and (accompaniment_cached.exists() or no_vocals_cached.exists()):
        info(f"Using cached separation results from {demucs_output_dir}")
        accompaniment_path = accompaniment_cached if accompaniment_cached.exists() else no_vocals_cached
        return str(vocals_cached), str(accompaniment_path)
    
    # 构建 Demucs 命令
    cmd = [
        "demucs",
        "--two-stems=vocals",
        "--name", model_name,
        "-o", str(output_dir_path),
        str(input_file),
    ]
    
    info(f"Separating vocals from {input_file.name} using Demucs ({model_name})...")
    
    # 运行 Demucs 命令
    # 注意：需要 torchaudio==2.0.2（不使用 torchcodec）和 soundfile
    try:
        result = subprocess.run(
            cmd,
            check=True,  # 必须成功
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        error(f"Demucs separation failed:")
        error(f"  Command: {' '.join(cmd)}")
        error(f"  Return code: {e.returncode}")
        if e.stderr:
            error(f"  Error output: {e.stderr[-1000:]}")
        if e.stdout:
            error(f"  Standard output: {e.stdout[-500:]}")
        raise RuntimeError(
            f"Failed to separate vocals from {input_path}. "
            f"Demucs error (return code {e.returncode}). "
            f"This is likely due to torchcodec dependency issues. "
            f"Please downgrade torchaudio: pip install torchaudio==2.0.2"
        ) from e
    
    # 验证输出文件
    if not vocals_cached.exists():
        raise RuntimeError(
            f"Vocal separation failed: vocals.wav was not created in {demucs_output_dir}. "
            f"Please ensure torchaudio==2.0.2 and soundfile are installed"
        )
    
    if not accompaniment_cached.exists() and not no_vocals_cached.exists():
        raise RuntimeError(
            f"Vocal separation failed: accompaniment/no_vocals.wav was not created in {demucs_output_dir}"
        )
    
    accompaniment_path = accompaniment_cached if accompaniment_cached.exists() else no_vocals_cached
    
    vocals_size = vocals_cached.stat().st_size
    accompaniment_size = accompaniment_path.stat().st_size
    
    if vocals_size == 0:
        raise RuntimeError(f"Vocal separation failed: vocals.wav is empty")
    
    if accompaniment_size == 0:
        raise RuntimeError(f"Vocal separation failed: accompaniment.wav is empty")
    
    info(f"Vocal separation succeeded:")
    info(f"  Vocals: {vocals_cached.name} (size: {vocals_size / 1024 / 1024:.2f} MB)")
    info(f"  Accompaniment: {accompaniment_path.name} (size: {accompaniment_size / 1024 / 1024:.2f} MB)")
    
    return str(vocals_cached), str(accompaniment_path)
