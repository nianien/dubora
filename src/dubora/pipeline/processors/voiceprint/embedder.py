"""
声纹 embedding 提取器（ECAPA-TDNN via speechbrain）

从 vocals.wav 中截取指定 speaker 的音频片段，拼接后提取 192 维 embedding。
"""
import numpy as np
import torch
import torchaudio
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dubora.utils.logger import info, warning

# Lazy-loaded singleton to avoid repeated model loading
_classifier = None
_EMBEDDING_DIM = 192
_MIN_DURATION_S = 2.0  # 最少需要 2 秒音频


def _get_classifier():
    """Lazy-load speechbrain EncoderClassifier (ECAPA-TDNN)."""
    global _classifier
    if _classifier is None:
        from speechbrain.inference.speaker import EncoderClassifier
        _classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": "cpu"},
        )
        info("Loaded ECAPA-TDNN speaker encoder")
    return _classifier


def _extract_segments_audio(
    vocals_path: str,
    segments: List[Dict],
    speaker_id: str,
    sample_rate: int = 16000,
) -> Tuple[Optional[torch.Tensor], float]:
    """
    从 vocals.wav 截取指定 speaker 的所有片段并拼接。

    Args:
        vocals_path: vocals.wav 路径
        segments: ASR segments（含 speaker, start_ms, end_ms）
        speaker_id: 目标 speaker 标识
        sample_rate: 目标采样率

    Returns:
        (拼接后的 waveform tensor [1, T], 总时长秒数)
        如果无匹配片段返回 (None, 0.0)
    """
    waveform, sr = torchaudio.load(vocals_path)

    # 单声道
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # 重采样
    if sr != sample_rate:
        resampler = torchaudio.transforms.Resample(sr, sample_rate)
        waveform = resampler(waveform)

    chunks = []
    for seg in segments:
        seg_speaker = str(seg.get("speaker", ""))
        if seg_speaker != speaker_id:
            continue

        start_ms = seg.get("start_ms", 0)
        end_ms = seg.get("end_ms", start_ms)
        start_sample = int(start_ms * sample_rate / 1000)
        end_sample = int(end_ms * sample_rate / 1000)

        # 边界裁剪
        start_sample = max(0, start_sample)
        end_sample = min(waveform.shape[1], end_sample)

        if end_sample > start_sample:
            chunks.append(waveform[:, start_sample:end_sample])

    if not chunks:
        return None, 0.0

    concatenated = torch.cat(chunks, dim=1)
    duration_s = concatenated.shape[1] / sample_rate
    return concatenated, duration_s


def extract_speaker_embedding(
    vocals_path: str,
    segments: List[Dict],
    speaker_id: str,
    sample_rate: int = 16000,
) -> Optional[np.ndarray]:
    """
    提取指定 speaker 的声纹 embedding（192 维）。

    Args:
        vocals_path: vocals.wav 路径
        segments: ASR segments（含 speaker, start_ms, end_ms）
        speaker_id: 目标 speaker 标识
        sample_rate: 采样率

    Returns:
        192 维 numpy array，如果音频不足则返回 None
    """
    audio, duration_s = _extract_segments_audio(
        vocals_path, segments, speaker_id, sample_rate
    )

    if audio is None or duration_s < _MIN_DURATION_S:
        warning(
            f"Speaker {speaker_id}: insufficient audio ({duration_s:.1f}s < {_MIN_DURATION_S}s), skipping embedding"
        )
        return None

    classifier = _get_classifier()

    with torch.no_grad():
        embedding = classifier.encode_batch(audio)
        # shape: [1, 1, 192] -> [192]
        emb_np = embedding.squeeze().cpu().numpy()

    # L2 归一化
    norm = np.linalg.norm(emb_np)
    if norm > 0:
        emb_np = emb_np / norm

    return emb_np


def get_speaker_duration(
    segments: List[Dict],
    speaker_id: str,
) -> float:
    """计算指定 speaker 的总时长（秒）。"""
    total_ms = 0.0
    for seg in segments:
        if str(seg.get("speaker", "")) == speaker_id:
            start_ms = seg.get("start_ms", 0)
            end_ms = seg.get("end_ms", start_ms)
            total_ms += end_ms - start_ms
    return total_ms / 1000.0
