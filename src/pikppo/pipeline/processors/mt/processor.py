"""
MT Processor: 机器翻译（唯一对外入口）

职责：
- 接收 Phase 层的输入（segments）
- 调用内部实现进行翻译
- 返回 ProcessorResult（不负责文件 IO）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在 impl.py
- Phase 层只调用 processor.run()
"""
from typing import Any, Dict

from .._types import ProcessorResult
from .impl import translate_episode_segments


def run(
    segments: list[Dict[str, Any]],
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    target_cps: str = "12-17",
    avoid_formal: bool = True,
    profanity_policy: str = "soften",
) -> ProcessorResult:
    """
    翻译整集 segments（Stage 1 + Stage 2）。
    
    Args:
        segments: 中文 segments 列表（每个包含 text, start, end, speaker 等）
        api_key: OpenAI API key
        model: 模型名称
        temperature: 温度参数
        max_chars_per_line: 每行最大字符数
        max_lines: 每段最大行数
        target_cps: 目标字符每秒
        avoid_formal: 避免正式用语
        profanity_policy: 脏话处理策略
    
    Returns:
        ProcessorResult:
        - data.context: 翻译上下文字典
        - data.en_texts: 翻译后的英文文本列表（与 segments 顺序对应）
        - meta: 元数据（segments_count, translated_count 等）
    """
    context, en_texts = translate_episode_segments(
        segments=segments,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        target_cps=target_cps,
        avoid_formal=avoid_formal,
        profanity_policy=profanity_policy,
    )
    
    return ProcessorResult(
        outputs=[],  # 由 Phase 声明 outputs，processor 只负责业务处理
        data={
            "context": context,
            "en_texts": en_texts,
        },
        metrics={
            "segments_count": len(segments),
            "translated_count": len([t for t in en_texts if t]),
        },
    )
