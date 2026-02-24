"""
MT Processor: 机器翻译（唯一对外入口）

职责：
- 接收 Phase 层的输入（utterances 或 cues，来自 Subtitle Model）
- 支持两种翻译模式：
  1. utterance-level（推荐）：按 utterance 粒度翻译，支持语速预算、end_time 扩展、重断句
  2. cue-level（向后兼容）：按 cue 粒度翻译，带时间约束
- 返回 ProcessorResult（不负责文件 IO）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在：
  - utterance_translate.py（utterance-level 翻译）
  - time_aware_impl.py（cue-level 时间感知翻译）
  - impl.py（批量翻译，向后兼容）
- Phase 层只调用 processor.run()

字幕翻译以时间轴为第一约束：
- utterance-level：根据语速预算控制翻译长度，支持 end_time 扩展和重断句
- cue-level：每条 cue 的翻译必须满足 CPS 与最大字符限制
"""
from typing import Any, Dict, List, Optional

from .._types import ProcessorResult
from .utterance_translate import translate_utterances
from .time_aware_impl import translate_cues_with_time_constraints
from .time_aware_translate import calculate_max_chars


def run(
    utterances: Optional[List[Dict[str, Any]]] = None,
    cues: Optional[List[Dict[str, Any]]] = None,
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    cps_limit: float = 15.0,
    max_retries: int = 2,
    use_utterance_level: bool = True,  # 默认使用 utterance-level 翻译
    use_time_aware: bool = True,  # cue-level 时使用时间感知翻译
) -> ProcessorResult:
    """
    翻译 utterances 或 cues。
    
    Args:
        utterances: Utterance 列表（来自 Subtitle Model v1.2），每个包含：
            - utt_id: Utterance ID
            - start_ms: 开始时间（毫秒）
            - end_ms: 结束时间（毫秒）
            - speech_rate: {"zh_tps": float}
            - cues: [{"start_ms": int, "end_ms": int, "source": {"text": str}, ...}]
        cues: Cue 列表（向后兼容，来自 Subtitle Model），每个包含：
            - 注意：v1.3 已移除 cue_id，使用 utterance 内的索引即可
            - start_ms: 开始时间（毫秒）
            - end_ms: 结束时间（毫秒）
            - source: {"lang": "zh", "text": "..."}
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
        cps_limit: CPS 限制（默认 15，推荐范围 12-17，仅用于 cue-level）
        max_retries: 最大重试次数（默认 2）
        use_utterance_level: 是否使用 utterance-level 翻译（默认 True）
        use_time_aware: cue-level 时是否使用时间感知翻译（默认 True）
    
    Returns:
        ProcessorResult:
        - data.translation_set: utterance-level 翻译结果（如果使用 utterance-level）
        - data.translations: cue-level 翻译结果列表（如果使用 cue-level）
        - metrics: 元数据
    """
    # 创建翻译函数
    from .time_aware_impl import create_translate_fn
    
    translate_fn = create_translate_fn(
        api_key=api_key,
        model=model,
        temperature=temperature,
    )
    
    # utterance-level 翻译（推荐）
    if use_utterance_level and utterances:
        translation_set = translate_utterances(utterances, translate_fn)
        
        # 转换为统一的 translations 格式（用于向后兼容）
        translations = []
        for utt_result in translation_set["by_utt"].values():
            for segment in utt_result["segments"]:
                translations.append({
                    "cue_index": segment.get("cue_index", 0),  # v1.3: 使用索引而不是 cue_id
                    "text": segment.get("text", ""),
                    "start_ms": segment.get("start_ms", 0),
                    "end_ms": segment.get("end_ms", 0),
                    "status": "ok",
                    "retries": utt_result["metrics"]["retries"],
                })
        
        return ProcessorResult(
            outputs=[],
            data={
                "translation_set": translation_set,
                "translations": translations,  # 向后兼容
            },
            metrics={
                "utterances_count": len(utterances),
                "segments_count": len(translations),
                "total_extend_ms": sum(
                    r["metrics"]["extend_ms"]
                    for r in translation_set["by_utt"].values()
                ),
                "total_retries": sum(
                    r["metrics"]["retries"]
                    for r in translation_set["by_utt"].values()
                ),
            },
        )
    
    # cue-level 翻译（向后兼容）
    if not cues:
        return ProcessorResult(
            outputs=[],
            data={"translations": []},
            metrics={"segments_count": 0},
        )
    
    if not use_time_aware:
        # 向后兼容：使用旧的批量翻译方式
        from .impl import translate_episode_segments
        
        # 转换为 segments 格式（向后兼容，使用索引作为 ID）
        segments = []
        for i, cue in enumerate(cues):
            segments.append({
                "id": f"cue_{i}",  # v1.3: 使用索引而不是 cue_id
                "start": cue.get("start_ms", 0) / 1000.0,
                "end": cue.get("end_ms", 0) / 1000.0,
                "text": cue.get("source", {}).get("text", ""),
                "speaker": "",  # v1.3: speaker 在 utterance 级别
            })
        
        context, en_texts = translate_episode_segments(
            segments=segments,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_chars_per_line=42,
            max_lines=2,
            target_cps="12-17",
            avoid_formal=True,
            profanity_policy="soften",
        )
        
        # 转换为新的格式
        translations = []
        for i, cue in enumerate(cues):
            start_ms = cue.get("start_ms", 0)
            end_ms = cue.get("end_ms", 0)
            duration_sec = (end_ms - start_ms) / 1000.0
            en_text = en_texts[i] if i < len(en_texts) else ""
            
            max_chars = calculate_max_chars(start_ms, end_ms, cps_limit)
            actual_chars = len(en_text)
            cps = actual_chars / duration_sec if duration_sec > 0 else 0.0
            
            translations.append({
                "cue_index": i,  # v1.3: 使用索引而不是 cue_id
                "text": en_text,
                "max_chars": max_chars,
                "actual_chars": actual_chars,
                "cps": cps,
                "status": "ok" if en_text else "skipped",
                "retries": 0,
            })
        
        return ProcessorResult(
            outputs=[],
            data={
                "translations": translations,
                "context": context,  # 向后兼容
            },
            metrics={
                "segments_count": len(cues),
                "translated_count": len([t for t in en_texts if t]),
            },
        )
    
    # cue-level 时间感知翻译（向后兼容）
    translations = translate_cues_with_time_constraints(
        cues=cues,
        api_key=api_key,
        model=model,
        temperature=temperature,
        cps_limit=cps_limit,
        max_retries=max_retries,
    )
    
    # 统计信息
    ok_count = sum(1 for r in translations if r["status"] == "ok")
    compressed_count = sum(1 for r in translations if r["status"] == "compressed")
    failed_count = sum(1 for r in translations if r["status"] in ["failed", "truncated"])
    skipped_count = sum(1 for r in translations if r["status"] == "skipped")
    
    return ProcessorResult(
        outputs=[],  # 由 Phase 声明 outputs，processor 只负责业务处理
        data={
            "translations": translations,
        },
        metrics={
            "segments_count": len(cues),
            "ok_count": ok_count,
            "compressed_count": compressed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "cps_limit": cps_limit,
        },
    )
