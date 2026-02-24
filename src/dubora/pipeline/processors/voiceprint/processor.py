"""
Voiceprint 处理器入口

串联 embedder → library → reference_clip，完成声纹识别全流程。
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from dubora.pipeline.processors._types import ProcessorResult
from dubora.utils.logger import info, warning

from .embedder import extract_speaker_embedding, get_speaker_duration
from .library import VoiceprintLibrary
from .reference_clip import export_reference_clip


def run_voiceprint(
    vocals_path: str,
    segments: List[Dict],
    library_path: str,
    output_dir: str,
    match_threshold: float = 0.65,
    ema_alpha: float = 0.3,
    ref_duration_s: float = 8.0,
) -> ProcessorResult:
    """
    声纹识别处理器：将 ASR 的匿名 speaker 映射到稳定角色 char_id。

    流程：
    1. 从 segments 中提取所有 unique speakers
    2. 对每个 speaker 提取 embedding
    3. 与剧级声纹库比对
    4. 匹配成功 -> 映射 speaker -> char_id，EMA 更新 embedding
       匹配失败 -> 注册新角色
    5. 导出参考音频片段
    6. 保存 speaker_map.json 和更新后的声纹库

    Args:
        vocals_path: vocals.wav 路径（sep 阶段输出）
        segments: ASR segments 列表（含 speaker, start_ms, end_ms, gender）
        library_path: 声纹库 JSON 路径（剧级）
        output_dir: 输出目录
        match_threshold: cosine similarity 匹配阈值
        ema_alpha: EMA 更新权重
        ref_duration_s: 参考音频目标时长

    Returns:
        ProcessorResult，data 包含 speaker_map 和 library
    """
    output = Path(output_dir)
    refs_dir = output / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)

    # 加载声纹库
    library = VoiceprintLibrary(library_path)

    # 收集 unique speakers
    speakers: Dict[str, Dict[str, Any]] = {}
    for seg in segments:
        spk = str(seg.get("speaker", ""))
        if not spk:
            continue
        if spk not in speakers:
            speakers[spk] = {
                "gender": seg.get("gender"),
            }

    info(f"Voiceprint: processing {len(speakers)} speakers")

    # 对每个 speaker 提取 embedding 并匹配
    speaker_map: Dict[str, str] = {}
    metrics = {
        "total_speakers": len(speakers),
        "matched": 0,
        "registered": 0,
        "skipped": 0,
    }

    for spk_id, spk_info in speakers.items():
        gender = spk_info.get("gender")
        duration_s = get_speaker_duration(segments, spk_id)

        # 提取 embedding
        embedding = extract_speaker_embedding(vocals_path, segments, spk_id)

        if embedding is None:
            warning(f"Speaker {spk_id}: embedding extraction failed, skipping")
            metrics["skipped"] += 1
            continue

        # 匹配声纹库
        char_id = library.match(embedding, threshold=match_threshold)

        if char_id is not None:
            # 匹配成功 -> EMA 更新
            library.update(char_id, embedding, duration_s=duration_s, alpha=ema_alpha)
            speaker_map[spk_id] = char_id
            metrics["matched"] += 1
        else:
            # 匹配失败 -> 注册新角色
            char_id = library.register(embedding, gender=gender, duration_s=duration_s)
            speaker_map[spk_id] = char_id
            metrics["registered"] += 1

        # 导出参考音频
        ref_path = str(refs_dir / f"{char_id}.wav")
        export_reference_clip(
            vocals_path=vocals_path,
            segments=segments,
            speaker_id=spk_id,
            output_path=ref_path,
            target_duration_s=ref_duration_s,
        )
        library.set_reference_clip(char_id, f"voiceprint/refs/{char_id}.wav")

    # 保存声纹库
    library.save()

    info(
        f"Voiceprint done: {metrics['matched']} matched, "
        f"{metrics['registered']} registered, {metrics['skipped']} skipped"
    )

    return ProcessorResult(
        outputs=["voiceprint.speaker_map", "voiceprint.reference_clips"],
        data={
            "speaker_map": speaker_map,
            "library": library,
        },
        metrics=metrics,
    )
