"""
时间感知的字幕级 MT：cue-level 翻译，带硬约束

核心原则：
- 字幕翻译以时间轴为第一约束：每条 cue 的翻译必须满足 CPS 与最大字符限制
- 采用受限翻译 + 程序校验 + 二次压缩策略
- 最终结果直接写回 Subtitle Model 的 target 字段

工程指标：
- CPS（Characters Per Second）：12-17 cps（英文字幕推荐）
- 最大行宽：单行 ≤ 42 chars，双行合计 ≤ 84 chars
- 时间窗：max_chars = floor(duration_sec * cps_limit)
"""
import math
from typing import Any, Callable, Dict, List, Optional

from pikppo.utils.logger import info, warning


def calculate_max_chars(
    start_ms: int,
    end_ms: int,
    cps_limit: float = 15.0,
) -> int:
    """
    根据时间窗计算最大允许字符数。
    
    Args:
        start_ms: 开始时间（毫秒）
        end_ms: 结束时间（毫秒）
        cps_limit: CPS 限制（默认 15，推荐范围 12-17）
    
    Returns:
        最大允许字符数（含空格）
    
    示例：
        start_ms=5280, end_ms=6580, cps_limit=15
        duration = 1.3s
        max_chars = floor(1.3 * 15) = 19
    """
    duration_sec = (end_ms - start_ms) / 1000.0
    if duration_sec <= 0:
        return 0
    
    max_chars = math.floor(duration_sec * cps_limit)
    return max(1, max_chars)  # 至少 1 个字符


def build_translation_prompt(
    zh_text: str,
    duration_sec: float,
    max_chars: int,
) -> str:
    """
    构建受限翻译 Prompt（单条 cue）。
    
    Args:
        zh_text: 中文原文
        duration_sec: 显示时长（秒）
        max_chars: 最大允许字符数
    
    Returns:
        Prompt 字符串
    """
    prompt = f"""You are translating Chinese subtitles into English for on-screen subtitles.

Constraints:
- This subtitle will be displayed for {duration_sec:.1f} seconds.
- Maximum allowed length: {max_chars} English characters (including spaces).
- The translation must be natural, concise, and readable.
- Do NOT add explanations or notes.
- Do NOT exceed the maximum length.

If the original meaning is long, prioritize clarity over completeness.

After generating the translation, silently verify that the length does not exceed {max_chars}.
If it does, rewrite it shorter.

Chinese subtitle:
"{zh_text}"

Output ONLY the English subtitle text."""
    
    return prompt


def build_compression_prompt(
    candidate: str,
    max_chars: int,
) -> str:
    """
    构建二次压缩 Prompt（兜底策略）。
    
    Args:
        candidate: 第一次翻译的结果（超长）
        max_chars: 最大允许字符数
    
    Returns:
        Prompt 字符串
    """
    prompt = f"""Shorten the following English subtitle to fit within {max_chars} characters,
while keeping the core meaning.

Subtitle:
"{candidate}"

Output ONLY the shortened subtitle."""
    
    return prompt


def should_allow_loose_translation(
    duration_sec: float,
    zh_text: str,
) -> bool:
    """
    判断是否允许"宽松翻译"（意译/省略修饰）。
    
    规则：
    - duration < 0.8s：允许意译/省略修饰
    - 呼喊/称呼（"哥""爸""平安哥"）：允许省略或简化
    - 情绪词 + 重复：只保留一个核心词
    
    Args:
        duration_sec: 显示时长（秒）
        zh_text: 中文原文
    
    Returns:
        是否允许宽松翻译
    """
    if duration_sec < 0.8:
        return True
    
    # 检查是否是呼喊/称呼
    call_patterns = ["哥", "爸", "妈", "姐", "弟", "妹", "平安哥"]
    if any(pattern in zh_text for pattern in call_patterns):
        return True
    
    return False


def translate_cue_with_constraints(
    zh_text: str,
    start_ms: int,
    end_ms: int,
    *,
    translate_fn: Callable[[str], str],  # 翻译函数：prompt -> str
    cps_limit: float = 15.0,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """
    翻译单条 cue（带时间约束）。
    
    Args:
        zh_text: 中文原文
        start_ms: 开始时间（毫秒）
        end_ms: 结束时间（毫秒）
        translate_fn: 翻译函数，接受 prompt 字符串，返回翻译结果
        cps_limit: CPS 限制（默认 15）
        max_retries: 最大重试次数（默认 2，即最多尝试 3 次）
    
    Returns:
        翻译结果字典：
        {
            "text": "翻译文本",
            "max_chars": 19,
            "actual_chars": 18,
            "cps": 13.8,
            "status": "ok" | "compressed" | "failed",
            "retries": 1
        }
    """
    duration_sec = (end_ms - start_ms) / 1000.0
    max_chars = calculate_max_chars(start_ms, end_ms, cps_limit)
    
    # 阶段 A：初译（严格受限）
    prompt = build_translation_prompt(zh_text, duration_sec, max_chars)
    candidate = translate_fn(prompt)
    
    if not candidate:
        return {
            "text": "",
            "max_chars": max_chars,
            "actual_chars": 0,
            "cps": 0.0,
            "status": "failed",
            "retries": 0,
        }
    
    # 清理候选文本（去除可能的解释或标记）
    candidate = candidate.strip()
    # 移除可能的引号
    if candidate.startswith('"') and candidate.endswith('"'):
        candidate = candidate[1:-1]
    
    actual_chars = len(candidate)
    retries = 0
    
    # 阶段 B：程序校验
    if actual_chars <= max_chars:
        # 成功，直接返回
        cps = actual_chars / duration_sec if duration_sec > 0 else 0.0
        return {
            "text": candidate,
            "max_chars": max_chars,
            "actual_chars": actual_chars,
            "cps": cps,
            "status": "ok",
            "retries": retries,
        }
    
    # 超长，进行二次压缩
    if retries < max_retries:
        retries += 1
        compression_prompt = build_compression_prompt(candidate, max_chars)
        compressed = translate_fn(compression_prompt)
        
        if compressed:
            compressed = compressed.strip()
            if compressed.startswith('"') and compressed.endswith('"'):
                compressed = compressed[1:-1]
            
            compressed_chars = len(compressed)
            if compressed_chars <= max_chars:
                cps = compressed_chars / duration_sec if duration_sec > 0 else 0.0
                return {
                    "text": compressed,
                    "max_chars": max_chars,
                    "actual_chars": compressed_chars,
                    "cps": cps,
                    "status": "compressed",
                    "retries": retries,
                }
            else:
                # 压缩后仍然超长，截断（最后兜底）
                warning(
                    f"Translation still too long after compression: {compressed_chars} > {max_chars}. "
                    f"Truncating to {max_chars} chars."
                )
                truncated = compressed[:max_chars].rstrip()
                cps = len(truncated) / duration_sec if duration_sec > 0 else 0.0
                return {
                    "text": truncated,
                    "max_chars": max_chars,
                    "actual_chars": len(truncated),
                    "cps": cps,
                    "status": "truncated",
                    "retries": retries,
                }
    
    # 所有重试都失败，返回原始候选（即使超长）
    warning(
        f"Translation exceeds limit after {retries} retries: {actual_chars} > {max_chars}. "
        f"Using original translation."
    )
    cps = actual_chars / duration_sec if duration_sec > 0 else 0.0
    return {
        "text": candidate,
        "max_chars": max_chars,
        "actual_chars": actual_chars,
        "cps": cps,
        "status": "failed",
        "retries": retries,
    }


def translate_cues_time_aware(
    cues: List[Dict[str, Any]],
    *,
    translate_fn: Callable[[str], str],
    cps_limit: float = 15.0,
    max_retries: int = 2,
) -> List[Dict[str, Any]]:
    """
    批量翻译 cues（时间感知）。
    
    Args:
        cues: Cue 列表，每个包含：
            - cue_id: 字幕单元 ID
            - start_ms: 开始时间（毫秒）
            - end_ms: 结束时间（毫秒）
            - source: {"lang": "zh", "text": "..."}
        translate_fn: 翻译函数，接受 prompt 字符串，返回翻译结果
        cps_limit: CPS 限制（默认 15）
        max_retries: 最大重试次数（默认 2）
    
    Returns:
        翻译结果列表，每个包含：
        {
            "cue_id": "cue_0001",
            "text": "翻译文本",
            "max_chars": 19,
            "actual_chars": 18,
            "cps": 13.8,
            "status": "ok",
            "retries": 0
        }
    """
    results = []
    
    for cue in cues:
        cue_id = cue.get("cue_id", "")
        start_ms = cue.get("start_ms", 0)
        end_ms = cue.get("end_ms", 0)
        source = cue.get("source", {})
        zh_text = source.get("text", "")
        
        if not zh_text:
            results.append({
                "cue_id": cue_id,
                "text": "",
                "max_chars": 0,
                "actual_chars": 0,
                "cps": 0.0,
                "status": "skipped",
                "retries": 0,
            })
            continue
        
        result = translate_cue_with_constraints(
            zh_text=zh_text,
            start_ms=start_ms,
            end_ms=end_ms,
            translate_fn=translate_fn,
            cps_limit=cps_limit,
            max_retries=max_retries,
        )
        result["cue_id"] = cue_id
        results.append(result)
    
    return results
