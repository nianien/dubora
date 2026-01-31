"""
MT Processor: 机器翻译（唯一对外入口）

职责：
- 接收 Phase 层的输入（cues，来自 Subtitle Model）
- 调用时间感知翻译（cue-level，带硬约束）
- 返回 ProcessorResult（不负责文件 IO）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在 impl.py（批量翻译）和 time_aware_impl.py（时间感知翻译）
- Phase 层只调用 processor.run()

字幕翻译以时间轴为第一约束：
- 每条 cue 的翻译必须满足 CPS 与最大字符限制
- 采用受限翻译 + 程序校验 + 二次压缩策略
- 最终结果直接写回 Subtitle Model 的 target 字段
"""
from typing import Any, Dict, List

from .._types import ProcessorResult
from .time_aware_impl import translate_cues_with_time_constraints
from .time_aware_translate import calculate_max_chars


def run(
    cues: List[Dict[str, Any]],
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    cps_limit: float = 15.0,
    max_retries: int = 2,
    use_time_aware: bool = True,  # 默认使用时间感知翻译
) -> ProcessorResult:
    """
    翻译 cues（时间感知，cue-level）。
    
    Args:
        cues: Cue 列表（来自 Subtitle Model），每个包含：
            - cue_id: 字幕单元 ID
            - start_ms: 开始时间（毫秒）
            - end_ms: 结束时间（毫秒）
            - source: {"lang": "zh", "text": "..."}
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
        cps_limit: CPS 限制（默认 15，推荐范围 12-17）
        max_retries: 最大重试次数（默认 2）
        use_time_aware: 是否使用时间感知翻译（默认 True）
    
    Returns:
        ProcessorResult:
        - data.translations: 翻译结果列表，每个包含：
            {
                "cue_id": "cue_0001",
                "text": "翻译文本",
                "max_chars": 19,
                "actual_chars": 18,
                "cps": 13.8,
                "status": "ok" | "compressed" | "truncated" | "failed" | "skipped",
                "retries": 0
            }
        - metrics: 元数据（segments_count, ok_count, compressed_count, failed_count 等）
    """
    if not use_time_aware:
        # 向后兼容：使用旧的批量翻译方式
        from .impl import translate_episode_segments
        
        # 转换为 segments 格式
        segments = []
        for cue in cues:
            segments.append({
                "id": cue.get("cue_id", ""),
                "start": cue.get("start_ms", 0) / 1000.0,
                "end": cue.get("end_ms", 0) / 1000.0,
                "text": cue.get("source", {}).get("text", ""),
                "speaker": cue.get("speaker", ""),
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
                "cue_id": cue.get("cue_id", ""),
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
    
    # 时间感知翻译（推荐）
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
