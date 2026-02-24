"""
声线分配：为每个 speaker 分配 voice

支持两种模式：
1. 无声纹映射：按 speaker 交替分配（原始行为）
2. 有声纹映射：按 char_id 作为稳定 key 分配，跨集复用同一 voice_id
"""
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional

from dubora.models.voice_pool import VoicePool
from dubora.utils.logger import info


def _load_voice_assignment_by_char(
    voice_assignment_path: str,
) -> Dict[str, Dict[str, str]]:
    """加载剧级 char_id → voice 映射（如果存在）。"""
    path = Path(voice_assignment_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("characters", {})


def assign_voices(
    segments_path: str,
    reference_audio_path: Optional[str],
    voice_pool_path: Optional[str],
    output_dir: str,
    speaker_map_path: Optional[str] = None,
    series_voice_assignment_path: Optional[str] = None,
) -> str:
    """
    为每个 speaker 分配 voice。

    如果提供了 speaker_map_path，则使用 char_id 作为稳定 key：
    - 同一 char_id 跨集使用同一 voice_id
    - 新 char_id 按交替策略分配新 voice

    Args:
        segments_path: segments JSON 文件路径
        reference_audio_path: 参考音频路径（可选，用于性别检测）
        voice_pool_path: voice pool JSON 文件路径（可选）
        output_dir: 输出目录
        speaker_map_path: voiceprint speaker_map.json 路径（可选）
        series_voice_assignment_path: 剧级 voice_assignment.json 路径（可选）

    Returns:
        voice_assignment.json 文件路径
    """
    # 读取 segments
    with open(segments_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    # 加载 voice pool
    voice_pool = VoicePool(pool_path=voice_pool_path)

    # 加载 speaker_map（speaker → char_id）
    speaker_map: Dict[str, str] = {}
    if speaker_map_path:
        sm_path = Path(speaker_map_path)
        if sm_path.exists():
            with open(sm_path, "r", encoding="utf-8") as f:
                sm_data = json.load(f)
            speaker_map = sm_data.get("speaker_map", {})
            info(f"Loaded speaker_map: {len(speaker_map)} mappings")

    # 加载剧级 char_id → voice 映射（跨集复用）
    char_voice_map: Dict[str, Dict[str, str]] = {}
    if series_voice_assignment_path:
        char_voice_map = _load_voice_assignment_by_char(series_voice_assignment_path)
        if char_voice_map:
            info(f"Loaded series voice assignment: {len(char_voice_map)} characters")

    # 统计每个 speaker 的总时长
    speaker_durations: Dict[str, float] = {}
    for seg in segments:
        speaker = seg.get("speaker", "speaker_0")
        duration = seg.get("end", 0.0) - seg.get("start", 0.0)
        speaker_durations[speaker] = speaker_durations.get(speaker, 0.0) + duration

    # 按时长排序（降序）
    sorted_speakers = sorted(
        speaker_durations.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    # 获取 voice pool 中的 voices
    pool_voices = voice_pool.get_all_voices()
    male_voices = [v for v in pool_voices if v.get("gender") == "male"]
    female_voices = [v for v in pool_voices if v.get("gender") == "female"]

    # 如果没有 voices，使用默认值
    if not pool_voices:
        default_male = {"voice_id": "en-US-GuyNeural", "name": "Guy", "gender": "male"}
        default_female = {"voice_id": "en-US-JennyNeural", "name": "Jenny", "gender": "female"}
        male_voices = [default_male]
        female_voices = [default_female]
        pool_voices = [default_male, default_female]

    # 记录已使用的 voice_id（避免重复分配）
    used_voice_ids = set()
    for v in char_voice_map.values():
        used_voice_ids.add(v.get("voice_id"))

    # 分配策略
    assignment: Dict[str, Dict[str, str]] = {}

    for rank, (speaker, duration) in enumerate(sorted_speakers):
        # 如果有声纹映射，优先使用 char_id 查找已有分配
        char_id = speaker_map.get(speaker)
        if char_id and char_id in char_voice_map:
            voice_info = char_voice_map[char_id]
            assignment[speaker] = {
                "voice_id": voice_info.get("voice_id", "en-US-GuyNeural"),
                "name": voice_info.get("name", "Guy"),
                "gender": voice_info.get("gender", "male"),
            }
            continue

        # 交替分配（原始逻辑）
        if rank < len(male_voices) + len(female_voices):
            if rank % 2 == 0:
                voice_idx = rank // 2
                if voice_idx < len(male_voices):
                    voice = male_voices[voice_idx]
                else:
                    voice = male_voices[-1] if male_voices else pool_voices[0]
            else:
                voice_idx = rank // 2
                if voice_idx < len(female_voices):
                    voice = female_voices[voice_idx]
                else:
                    voice = female_voices[-1] if female_voices else pool_voices[0]
        else:
            speaker_hash = int(hashlib.md5(speaker.encode()).hexdigest(), 16)
            if speaker_hash % 2 == 0:
                voice = male_voices[0] if male_voices else pool_voices[0]
            else:
                voice = female_voices[0] if female_voices else pool_voices[0]

        voice_entry = {
            "voice_id": voice.get("voice_id", voice.get("name", "en-US-GuyNeural")),
            "name": voice.get("name", "Guy"),
            "gender": voice.get("gender", "male"),
        }
        assignment[speaker] = voice_entry

        # 记录 char_id → voice 映射（供跨集复用）
        if char_id:
            char_voice_map[char_id] = voice_entry

    # 保存 assignment
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    assignment_path = output_path / "voice-assignment.json"

    assignment_data = {
        "speakers": assignment,
        "total_speakers": len(assignment),
    }

    with open(assignment_path, "w", encoding="utf-8") as f:
        json.dump(assignment_data, f, indent=2, ensure_ascii=False)

    # 保存剧级 char_id → voice 映射
    if series_voice_assignment_path and char_voice_map:
        series_path = Path(series_voice_assignment_path)
        series_path.parent.mkdir(parents=True, exist_ok=True)
        with open(series_path, "w", encoding="utf-8") as f:
            json.dump(
                {"characters": char_voice_map},
                f,
                indent=2,
                ensure_ascii=False,
            )
        info(f"Saved series voice assignment: {series_path} ({len(char_voice_map)} characters)")

    info(f"Voice assignment saved: {assignment_path} ({len(assignment)} speakers)")

    return str(assignment_path)
