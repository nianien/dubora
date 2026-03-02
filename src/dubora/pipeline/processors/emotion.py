"""
Emotion Correct Processor: LLM 情绪修正（无状态逻辑）

职责：
- 将所有 speech segment 的台词、角色、当前情绪发送给 LLM
- LLM 根据语义判断并返回需要修正的段落
- 校验返回的 emotion 值在合法列表内

不负责：
- 文件 I/O、manifest 更新（由 Phase 层负责）
- LLM 客户端初始化（由调用方注入 translate_fn）
"""
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from dubora.prompts import load_prompt
from dubora.schema.asr_model import AsrSegment
from dubora.utils.logger import info, warning

# 英文 TTS 支持的 emotion
VALID_EMOTIONS = frozenset({
    "neutral", "chat", "happy", "angry", "sad",
    "excited", "affectionate", "warm", "ASMR",
})


@dataclass
class EmotionResult:
    """情绪修正结果。"""
    corrections: Dict[str, str] = field(default_factory=dict)
    corrected_count: int = 0


def _parse_llm_response(raw: str) -> list | None:
    """解析 LLM 返回的 JSON，处理 markdown code fence。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass
    return None


def run(
    segments: List[AsrSegment],
    translate_fn: Callable,
) -> EmotionResult:
    """执行情绪修正。

    Args:
        segments: 当前 dub.json 中的 segments
        translate_fn: LLM 调用函数（prompt -> response_text）

    Returns:
        EmotionResult，包含修正映射和计数
    """
    # 筛选 speech segments
    speech_segs = [s for s in segments if s.type == "speech"]
    if not speech_segs:
        info("Emotion correct: no speech segments, skipping")
        return EmotionResult()

    # 构造 prompt 数据
    segments_for_prompt = [
        {
            "id": seg.id,
            "text": seg.text,
            "speaker": seg.speaker,
            "emotion": seg.emotion,
        }
        for seg in speech_segs
    ]

    prompt_data = load_prompt(
        "emotion_correct",
        segments_json=json.dumps(segments_for_prompt, ensure_ascii=False, indent=2),
    )
    prompt_text = prompt_data.system + "\n\n" + prompt_data.user

    # 调用 LLM
    try:
        raw_response = translate_fn(prompt_text)
    except Exception as e:
        warning(f"LLM call failed for emotion correct: {e}")
        return EmotionResult()

    # 解析响应
    info(f"Emotion correct LLM response ({len(raw_response)} chars): {raw_response[:300]}...")
    items = _parse_llm_response(raw_response)
    if items is None:
        warning(f"Failed to parse emotion correct response as JSON: {raw_response[:200]}")
        return EmotionResult()

    # 建立有效 segment id 集合
    valid_ids = {seg.id for seg in speech_segs}

    # 校验并收集修正
    corrections: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        emotion = item.get("emotion")
        if not seg_id or not emotion:
            continue
        if seg_id not in valid_ids:
            warning(f"Emotion correct: unknown segment id '{seg_id}', skipping")
            continue
        if emotion not in VALID_EMOTIONS:
            warning(f"Emotion correct: invalid emotion '{emotion}' for {seg_id}, skipping")
            continue
        corrections[seg_id] = emotion

    return EmotionResult(
        corrections=corrections,
        corrected_count=len(corrections),
    )
