"""
参考音频片段导出

从 vocals.wav 中提取指定 speaker 的最佳片段，拼接到目标时长，
输出 16kHz mono WAV 供 TTS 声线克隆使用。
"""
import subprocess
import torch
import torchaudio
from pathlib import Path
from typing import Dict, List

from dubora.utils.logger import info


def export_reference_clip(
    vocals_path: str,
    segments: List[Dict],
    speaker_id: str,
    output_path: str,
    target_duration_s: float = 8.0,
    sample_rate: int = 16000,
) -> str:
    """
    导出指定 speaker 的参考音频片段。

    策略：
    1. 收集该 speaker 的所有片段，按时长降序排列
    2. 从最长片段开始累加，直到达到目标时长
    3. 输出 16kHz mono WAV

    Args:
        vocals_path: vocals.wav 路径
        segments: ASR segments（含 speaker, start_ms, end_ms）
        speaker_id: 目标 speaker 标识
        output_path: 输出 WAV 路径
        target_duration_s: 目标时长（秒），默认 8s
        sample_rate: 输出采样率

    Returns:
        输出文件路径
    """
    waveform, sr = torchaudio.load(vocals_path)

    # 单声道
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # 重采样
    if sr != sample_rate:
        resampler = torchaudio.transforms.Resample(sr, sample_rate)
        waveform = resampler(waveform)

    # 收集该 speaker 的片段
    speaker_chunks = []
    for seg in segments:
        if str(seg.get("speaker", "")) != speaker_id:
            continue
        start_ms = seg.get("start_ms", 0)
        end_ms = seg.get("end_ms", start_ms)
        duration_ms = end_ms - start_ms
        if duration_ms <= 0:
            continue

        start_sample = int(start_ms * sample_rate / 1000)
        end_sample = int(end_ms * sample_rate / 1000)
        start_sample = max(0, start_sample)
        end_sample = min(waveform.shape[1], end_sample)

        if end_sample > start_sample:
            speaker_chunks.append({
                "audio": waveform[:, start_sample:end_sample],
                "duration_ms": duration_ms,
            })

    if not speaker_chunks:
        info(f"No audio segments found for speaker {speaker_id}")
        return output_path

    # 按时长降序排列（优先选择长片段，因为通常更清晰）
    speaker_chunks.sort(key=lambda x: x["duration_ms"], reverse=True)

    # 累加片段直到达到目标时长
    target_samples = int(target_duration_s * sample_rate)
    selected = []
    total_samples = 0

    for chunk in speaker_chunks:
        audio = chunk["audio"]
        remaining = target_samples - total_samples
        if remaining <= 0:
            break
        # 截取所需部分
        take = min(audio.shape[1], remaining)
        selected.append(audio[:, :take])
        total_samples += take

    if not selected:
        info(f"No valid audio for speaker {speaker_id}")
        return output_path

    # 拼接
    concatenated = torch.cat(selected, dim=1)

    # 保存
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(str(output), concatenated, sample_rate)

    actual_duration = concatenated.shape[1] / sample_rate
    info(f"Exported reference clip: {output_path} ({actual_duration:.1f}s)")

    return output_path
