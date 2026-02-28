"""
Reseg Processor: LLM 断句（无状态逻辑）

职责：
- 筛选过长的 ASR 段落（超过字数/时长阈值）
- 调用 LLM 进行断句
- 利用 ASR word-level 时间戳为子段分配精确时间
- 校验拆分一致性（文本完整性、最小字数/时长）

不负责：
- 文件 I/O、manifest 更新（由 Phase 层负责）
- LLM 客户端初始化（由调用方注入 translate_fn）
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from dubora.prompts import load_prompt
from dubora.schema.asr_model import AsrSegment, _gen_seg_id
from dubora.utils.logger import info, warning, error


@dataclass
class ResegResult:
    """断句处理结果。"""
    new_segments: List[AsrSegment]
    candidates_count: int = 0
    split_count: int = 0
    new_segments_count: int = 0


def _strip_punct(text: str) -> str:
    """去除所有标点符号，只保留字母/数字/汉字。"""
    return "".join(
        ch for ch in text
        if not unicodedata.category(ch).startswith("P")
        and not unicodedata.category(ch).startswith("S")
        and not ch.isspace()
    )


def _chinese_char_count(text: str) -> int:
    """统计中文字符数（CJK Unified Ideographs）。"""
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def _parse_llm_response(raw: str) -> Optional[list]:
    """解析 LLM 返回的 JSON，处理 markdown code fence。"""
    text = raw.strip()
    # 去除 markdown code fence
    if text.startswith("```"):
        lines = text.split("\n")
        # 去除首尾 ``` 行
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
        # 尝试从文本中提取 JSON 数组
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass
    return None


def _build_word_list(asr_result: dict) -> list:
    """从 ASR result 中提取所有 word-level 时间戳。

    返回: [{"text": "你", "start_time": 1230, "end_time": 1450}, ...]
    """
    words = []
    utterances = asr_result.get("result", {}).get("utterances", [])
    for utt in utterances:
        for w in utt.get("words", []):
            words.append({
                "text": w.get("text", ""),
                "start_time": w.get("start_time", 0),
                "end_time": w.get("end_time", 0),
            })
    return words


def _assign_words_to_segment(
    seg: AsrSegment,
    all_words: list,
) -> list:
    """按时间范围将 words 分配到 segment。

    匹配条件: seg.start_ms <= word.start_time < seg.end_ms
    """
    result = []
    for w in all_words:
        if w["start_time"] >= seg.start_ms and w["start_time"] < seg.end_ms:
            result.append(w)
    return result


def _map_splits_to_timestamps(
    splits: List[str],
    words: list,
) -> Optional[List[dict]]:
    """逐字符消耗 words，为每个 split 分配时间戳。

    ASR word-level 数据可能与 segment 文本不完全一致（ASR 引擎丢字），
    遇到匹配不上的字符直接跳过，不影响边界时间戳的确定。

    返回: [{"text": "子句", "start_ms": 1230, "end_ms": 3450}, ...]
    如果无法完成映射则返回 None。
    """
    if not words:
        return None

    result = []
    word_idx = 0
    char_in_word = 0  # 当前 word 中已消耗的字符偏移

    for split_text in splits:
        clean = _strip_punct(split_text)
        if not clean:
            # 空文本的 split，跳过
            continue

        first_word_idx = None
        last_word_idx = None
        chars_matched = 0
        chars_skipped = 0

        for ch in clean:
            # 在 words 中查找匹配的字符
            matched = False
            # 保存当前位置，用于回溯（避免消耗不属于当前字符的 words）
            saved_word_idx = word_idx
            saved_char_in_word = char_in_word

            while word_idx < len(words):
                word_text = _strip_punct(words[word_idx]["text"])
                if not word_text:
                    word_idx += 1
                    char_in_word = 0
                    continue

                if char_in_word < len(word_text) and word_text[char_in_word] == ch:
                    if first_word_idx is None:
                        first_word_idx = word_idx
                    last_word_idx = word_idx
                    chars_matched += 1
                    char_in_word += 1
                    if char_in_word >= len(word_text):
                        word_idx += 1
                        char_in_word = 0
                    matched = True
                    break
                else:
                    # word 中的字符不匹配，尝试跳过
                    char_in_word += 1
                    if char_in_word >= len(word_text):
                        word_idx += 1
                        char_in_word = 0

            if not matched:
                # ASR words 中找不到这个字，跳过继续（ASR 丢字属于正常现象）
                chars_skipped += 1
                # 回溯到搜索前的位置，避免 words 指针被无效推进
                word_idx = saved_word_idx
                char_in_word = saved_char_in_word

        if chars_skipped:
            info(f"  Timestamp mapping: skipped {chars_skipped} unmatched chars in '{split_text[:20]}...'")

        if first_word_idx is None or last_word_idx is None:
            # 整个 split 一个字都匹配不上，才算失败
            warning(f"Timestamp mapping failed: no words matched for split '{split_text[:20]}...'")
            return None

        result.append({
            "text": split_text,
            "start_ms": words[first_word_idx]["start_time"],
            "end_ms": words[last_word_idx]["end_time"],
        })

    return result


def run(
    segments: List[AsrSegment],
    asr_result: dict,
    *,
    min_chars: int = 6,
    max_chars_trigger: int = 25,
    max_duration_trigger: int = 6000,
    translate_fn: Callable,
) -> ResegResult:
    """执行断句处理。

    Args:
        segments: 当前 dub.json 中的 segments
        asr_result: 原始 ASR 响应（含 word timestamps）
        min_chars: 拆分后每段最少中文字数（防止碎片）
        max_chars_trigger: 触发拆分的字数阈值
        max_duration_trigger: 触发拆分的时长阈值（ms）
        translate_fn: LLM 调用函数（prompt -> response_text）

    Returns:
        ResegResult，包含新的 segments 列表和统计信息
    """
    # 1. 解析 ASR words
    all_words = _build_word_list(asr_result)
    if not all_words:
        warning("No word-level timestamps found in ASR result, skipping reseg")
        return ResegResult(
            new_segments=list(segments),
            candidates_count=0,
        )

    info(f"Loaded {len(all_words)} words from ASR result")

    # 2. 建立 segment -> words 映射
    seg_words_map = {}
    for seg in segments:
        seg_words_map[seg.id] = _assign_words_to_segment(seg, all_words)

    # 3. 筛选候选段落
    candidates = []
    for seg in segments:
        if seg.type != "speech":
            continue
        char_count = _chinese_char_count(seg.text)
        duration_ms = seg.end_ms - seg.start_ms
        if char_count > max_chars_trigger or duration_ms > max_duration_trigger:
            candidates.append(seg)

    if not candidates:
        info("No segments exceed thresholds, skipping reseg")
        return ResegResult(
            new_segments=list(segments),
            candidates_count=0,
        )

    info(f"Found {len(candidates)} candidate segments for reseg "
         f"(thresholds: >{max_chars_trigger} chars or >{max_duration_trigger}ms)")

    # 4. 构建 prompt（附带字数和时长信息，帮助 LLM 理解拆分原因）
    segments_for_prompt = []
    for seg in candidates:
        char_count = _chinese_char_count(seg.text)
        duration_ms = seg.end_ms - seg.start_ms
        segments_for_prompt.append({
            "id": seg.id,
            "text": seg.text,
            "chars": char_count,
            "duration_ms": duration_ms,
        })
    prompt_data = load_prompt(
        "reseg_split",
        min_chars=str(min_chars),
        segments_json=json.dumps(segments_for_prompt, ensure_ascii=False, indent=2),
    )

    prompt_text = prompt_data.system + "\n\n" + prompt_data.user

    # 5. 调用 LLM
    try:
        raw_response = translate_fn(prompt_text)
    except Exception as e:
        error(f"LLM call failed for reseg: {e}")
        return ResegResult(
            new_segments=list(segments),
            candidates_count=len(candidates),
        )

    # 6. 解析 JSON 响应
    info(f"LLM raw response ({len(raw_response)} chars): {raw_response[:300]}...")
    llm_results = _parse_llm_response(raw_response)
    if llm_results is None:
        warning(f"Failed to parse LLM response as JSON, skipping reseg. Response: {raw_response[:200]}")
        return ResegResult(
            new_segments=list(segments),
            candidates_count=len(candidates),
        )

    # 7-10. 处理每个拆分结果
    # 建立 id -> LLM result 的映射
    llm_map = {}
    for item in llm_results:
        if isinstance(item, dict) and "id" in item and "splits" in item:
            llm_map[item["id"]] = item["splits"]

    info(f"LLM returned {len(llm_results)} items, {len(llm_map)} with valid id+splits")

    # 建立 id -> candidate segment 的映射
    candidate_map = {seg.id: seg for seg in candidates}

    # 诊断：检查 LLM 返回的 ID 是否能匹配到候选段落
    matched_ids = set(llm_map.keys()) & set(candidate_map.keys())
    unmatched_ids = set(llm_map.keys()) - set(candidate_map.keys())
    if unmatched_ids:
        warning(f"LLM returned {len(unmatched_ids)} unmatched IDs: {list(unmatched_ids)[:5]}")
    if not matched_ids:
        warning(f"No LLM IDs matched candidates. LLM IDs: {list(llm_map.keys())[:3]}, "
                f"Candidate IDs: {list(candidate_map.keys())[:3]}")

    split_plan = {}  # seg_id -> list of new AsrSegment
    split_count = 0
    new_seg_count = 0

    for seg_id, splits in llm_map.items():
        seg = candidate_map.get(seg_id)
        if seg is None:
            warning(f"LLM returned unknown segment id: {seg_id}, skipping")
            continue

        # 单元素 splits = 不拆分
        if not isinstance(splits, list) or len(splits) <= 1:
            info(f"  {seg_id}: LLM decided no split needed (splits={len(splits) if isinstance(splits, list) else 'N/A'})")
            continue

        # 7. 校验文本一致性
        joined = _strip_punct("".join(splits))
        original = _strip_punct(seg.text)
        if joined != original:
            warning(
                f"Text consistency check failed for {seg_id}: "
                f"joined='{joined[:30]}...' != original='{original[:30]}...', "
                f"skipping split"
            )
            continue

        # 8. 映射时间戳
        words = seg_words_map.get(seg_id, [])
        timestamp_result = _map_splits_to_timestamps(splits, words)
        if timestamp_result is None:
            warning(f"Timestamp mapping failed for {seg_id}, skipping split")
            continue

        # 9. 下限校验（只检查字数，TTS 无最短时长限制）
        # 原句超标时放宽下限：必须拆开，只防极端碎片（< 3 字）
        seg_chars = _chinese_char_count(seg.text)
        seg_duration = seg.end_ms - seg.start_ms
        is_over_limit = seg_chars > max_chars_trigger or seg_duration > max_duration_trigger
        effective_min = 3 if is_over_limit else min_chars

        valid = True
        for ts in timestamp_result:
            sub_chars = _chinese_char_count(ts["text"])
            if sub_chars < effective_min:
                warning(
                    f"Sub-segment too few chars for {seg_id}: "
                    f"'{ts['text'][:15]}...' ({sub_chars} chars, min={effective_min}), "
                    f"skipping entire split"
                )
                valid = False
                break

        if not valid:
            continue

        # 10. 生成新 segments
        new_segs = []
        for ts in timestamp_result:
            new_segs.append(AsrSegment(
                id=_gen_seg_id(),
                start_ms=ts["start_ms"],
                end_ms=ts["end_ms"],
                text=ts["text"],
                speaker=seg.speaker,
                emotion=seg.emotion,
                type=seg.type,
                gender=seg.gender,
            ))

        split_plan[seg_id] = new_segs
        split_count += 1
        new_seg_count += len(new_segs)
        info(f"  {seg_id}: split into {len(new_segs)} sub-segments")

    # 构建最终 segments 列表
    if not split_plan:
        info("No valid splits produced by LLM")
        return ResegResult(
            new_segments=list(segments),
            candidates_count=len(candidates),
            split_count=0,
        )

    final_segments = []
    for seg in segments:
        if seg.id in split_plan:
            final_segments.extend(split_plan[seg.id])
        else:
            final_segments.append(seg)

    info(
        f"Reseg complete: {split_count} segments split, "
        f"{new_seg_count} new sub-segments, "
        f"total segments: {len(segments)} -> {len(final_segments)}"
    )

    return ResegResult(
        new_segments=final_segments,
        candidates_count=len(candidates),
        split_count=split_count,
        new_segments_count=new_seg_count,
    )
