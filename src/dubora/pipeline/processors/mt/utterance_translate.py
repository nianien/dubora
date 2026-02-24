"""
基于 SSOT utterance 粒度的 MT 实现

核心原则：
1. 按照 SSOT 中 utterance 粒度进行翻译，保证 utterance 的时间窗口不变
2. 翻译时，根据 utterance 的语速 * 系数 k，控制翻译长度
3. 如果英文长度超限，则可以扩展 utterance 的 end_time（120-250ms，不重叠）
4. 如果不满足长度限制，则重新翻译
5. 翻译完成后，在 utterance 时间窗口内允许语义重断句

工程指标：
- 语速分档：fast (≥5.5 tps, k=1.0), normal (4.0-5.5, k=1.15), slow (<4.0, k=1.2)
- 英文时长估计：en_cps = 14（字符/秒）
- end_time 扩展上限：120-250ms（默认 200ms）
- 安全间隔：60ms（防止与下一句重叠）
"""
import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from dubora.prompts import load_prompt, load_shared
from dubora.utils.logger import info, warning

if TYPE_CHECKING:
    from dubora.pipeline.processors.mt.dict_loader import DictLoader


# 语速分档阈值（可配置）
SPEECH_RATE_FAST_THRESHOLD = 5.5  # ≥5.5 tps 为 fast
SPEECH_RATE_NORMAL_THRESHOLD = 4.0  # 4.0-5.5 为 normal，<4.0 为 slow

# k 值（语速系数）
K_FAST = 1.0
K_NORMAL = 1.15
K_SLOW = 1.2

# 英文时长估计参数
EN_CPS = 14.0  # 英文字符/秒（不含空格）

# end_time 扩展参数
EXTEND_CAP_MS = 200  # 扩展上限（120-250ms 中间值）
SAFETY_GAP_MS = 60  # 安全间隔（防止重叠）

# 重翻译最大次数
MAX_RETRIES = 3

# 赌博/牌局关键词（触发 domain hint）
# 注意：「万」「条」不能放，它们作为量词太常见（五万=50K，三条路=three roads）
_GAMBLING_KEYWORDS = (
    "牌", "胡", "杠", "碰", "筒", "饼",
    "钓", "截胡", "点炮", "放炮", "屁胡", "自摸",
    "地胡", "天胡", "听牌",
    "筹码", "庄", "对家", "起手", "翻牌",
    "尖", "蓝道", "下注", "all-in", "底注",
)


def clean_translation_output(text: str) -> str:
    """
    清理翻译输出，移除所有系统标记。

    移除：
    - <sep> 标记
    - <<NAME_x>> 占位符
    - <<NAME_x:...>> 占位符
    - <SLANG:...> 标记

    Args:
        text: 原始翻译输出

    Returns:
        清理后的文本
    """
    if not text:
        return text

    # 移除 <sep> 标记（可能带空格）
    text = re.sub(r'\s*<sep>\s*', ' ', text)

    # 移除 NAME 占位符（<<NAME_0>> 或 <<NAME_0:...>>）
    text = re.sub(r'<<NAME_\d+(?::[^>]*)?>>', '', text)

    # 移除 SLANG 标记（<SLANG:key>）
    text = re.sub(r'<SLANG:[^>]+>', '', text)

    # 清理多余空格
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()

    return text


def is_only_punctuation(text: str) -> bool:
    """
    检查文本是否只包含标点符号和空白字符（没有实际单词）。

    Args:
        text: 要检查的文本

    Returns:
        True 如果文本只包含标点/空白，False 如果有实际内容
    """
    if not text:
        return True
    # 移除所有标点符号和空白字符，检查是否还有内容
    text_without_punc = re.sub(r'[^\w\s]', '', text)
    text_without_punc = re.sub(r'\s+', '', text_without_punc)
    return not text_without_punc


def pick_k(zh_tps: float) -> float:
    """
    根据语速选择 k 值。

    Args:
        zh_tps: 中文 tokens per second

    Returns:
        k 值（1.0 / 1.15 / 1.2）
    """
    if zh_tps >= SPEECH_RATE_FAST_THRESHOLD:
        return K_FAST  # 语速过快
    elif zh_tps >= SPEECH_RATE_NORMAL_THRESHOLD:
        return K_NORMAL  # 正常
    else:
        return K_SLOW  # 偏慢


def estimate_en_duration_ms(en_text: str) -> float:
    """
    估计英文文本的时长（毫秒）。

    使用字符速（CPS）估计，不含空格。

    Args:
        en_text: 英文文本

    Returns:
        预计时长（毫秒）
    """
    # 只计算字母和数字（不含空格和标点）
    en_chars = len(re.sub(r'[^a-zA-Z0-9]', '', en_text))
    if en_chars == 0:
        return 0.0

    # 使用 CPS 估计
    en_est_sec = en_chars / EN_CPS
    en_est_ms = en_est_sec * 1000.0

    return en_est_ms


def calculate_extend_ms(
    need_ms: float,
    end_ms: int,
    next_utt_start_ms: Optional[int],
) -> float:
    """
    计算可扩展的时间（不重叠）。

    Args:
        need_ms: 需要额外的时间（毫秒）
        end_ms: 当前 utterance 的结束时间（毫秒）
        next_utt_start_ms: 下一个 utterance 的开始时间（毫秒，None 表示没有下一句）

    Returns:
        可扩展的时间（毫秒，0 表示无法扩展）
    """
    # 扩展上限
    extend_cap_ms = EXTEND_CAP_MS

    # 计算不与下一句重叠的最大扩展时间
    if next_utt_start_ms is not None:
        no_overlap_cap_ms = next_utt_start_ms - end_ms - SAFETY_GAP_MS
        if no_overlap_cap_ms < 0:
            return 0.0  # 无法扩展（会重叠）
        extend_cap_ms = min(extend_cap_ms, no_overlap_cap_ms)

    # 最终可扩展时间
    extend_ms = min(need_ms, extend_cap_ms)
    return max(0.0, extend_ms)


def build_utterance_translation_prompt(
    zh_text: str,
    budget_ms: float,
    retry_level: int = 0,
    *,
    episode_context: str = "",
    plot_overview: str = "",
    slang_glossary_text: str = "",
    is_gemini: bool = False,
) -> str:
    """
    构建 utterance 翻译 Prompt（从 YAML 模板加载）。

    Args:
        zh_text: 中文文本（utterance 内所有 cues 合并）
        budget_ms: 时间预算（毫秒）
        retry_level: 重试级别（0=首次，1=压缩，2=更强压缩）
        episode_context: 整集对话上下文（从 asr.result.text 获取）
        plot_overview: 剧情简介（可选）
        slang_glossary_text: 行话词表文本（从 DictLoader 获取）

    Returns:
        Prompt 字符串
    """
    budget_sec = budget_ms / 1000.0
    max_chars = int((budget_ms / 1000.0) * EN_CPS)

    if retry_level == 0:
        # 构建可选块
        glossary_block = ""
        if slang_glossary_text:
            glossary_block = (
                "Glossary — only apply if the exact source phrase appears in THIS utterance. "
                "Do not infer or generalize:\n" + slang_glossary_text
            )

        # Domain hint（按需注入：只在源文本含赌博/牌局关键词时添加）
        gambling_domain_hint = ""
        has_gambling = any(kw in zh_text for kw in _GAMBLING_KEYWORDS)
        if has_gambling:
            gambling_domain_hint = (
                "Context: This is a GAMBLING SCENE in a Chinese drama.\n"
                "Determine the game type from context (mahjong, poker, card games, etc.) "
                "and translate accordingly.\n"
                "IMPORTANT: [Number]+万/条 can mean MONEY or TILES depending on context:\n"
                "- With verbs 借/给/欠/输/赢/还 → MONEY (e.g. 借五万 = borrow fifty grand)\n"
                "- With verbs 打/出/摸/钓 or said alone as a call → TILE (e.g. 打一万 = 1-Character)\n"
                "When in doubt, use episode context to decide.\n\n"
                + load_shared("gambling_glossary")
            )

        plot_overview_block = ""
        if plot_overview:
            plot_overview_block = f"Plot overview:\n{plot_overview}\n"

        episode_context_block = ""
        if episode_context:
            max_context_chars = 5000
            ctx = episode_context[:max_context_chars] + "..." if len(episode_context) > max_context_chars else episode_context
            episode_context_block = f"Episode dialogue context:\n{ctx}\n"

        p = load_prompt("mt_utterance_translate.default",
            name_placeholder_rules=load_shared("name_placeholder_rules"),
            glossary_block=glossary_block,
            gambling_domain_hint=gambling_domain_hint,
            no_chinese_policy=load_shared("no_chinese_policy"),
            plot_overview_block=plot_overview_block,
            episode_context_block=episode_context_block,
            budget_sec=f"{budget_sec:.2f}",
            max_chars=str(max_chars),
            input_text=zh_text,
        )
        prompt = p.system + "\n\n" + p.user
    elif retry_level == 1:
        p = load_prompt("mt_utterance_translate.retry_level_1",
            budget_sec=f"{budget_sec:.2f}",
            max_chars=str(max_chars),
            input_text=zh_text,
        )
        prompt = p.text
    else:
        p = load_prompt("mt_utterance_translate.retry_level_2",
            budget_sec=f"{budget_sec:.2f}",
            max_chars=str(max_chars),
            input_text=zh_text,
        )
        prompt = p.text

    return prompt


def translate_utterance_with_retry(
    zh_text: str,
    budget_ms: float,
    translate_fn: Callable[[str], str],
    max_retries: int = MAX_RETRIES,
    *,
    episode_context: str = "",
    plot_overview: str = "",
    slang_glossary_text: str = "",
    dict_loader: Optional['DictLoader'] = None,  # 用于校验
    is_retry: bool = False,  # 是否为重试
    violations: Optional[List[str]] = None,  # 违反的术语列表
    is_gemini: bool = False,  # 是否为 Gemini 模型
) -> Tuple[str, int]:
    """
    翻译 utterance（带重试）。

    Args:
        zh_text: 中文文本
        budget_ms: 时间预算（毫秒）
        translate_fn: 翻译函数
        max_retries: 最大重试次数
        episode_context: 整集对话上下文
        plot_overview: 剧情简介
        slang_glossary_text: 行话词表文本

    Returns:
        (翻译结果, 重试次数)
    """
    for retry in range(max_retries):
        # 如果是重试（glossary 违反），使用更严格的 prompt
        if is_retry and violations:
            # 构建重试 prompt（追加违反提示）
            prompt = build_utterance_translation_prompt(
                zh_text, budget_ms, retry_level=0,  # 使用首次 prompt
                episode_context=episode_context,
                plot_overview=plot_overview,
                slang_glossary_text=slang_glossary_text,
                is_gemini=is_gemini,
            )
            # 追加违反提示
            prompt += "\n\nIMPORTANT: You violated the glossary. The following mappings were not followed:\n"
            for violation in violations:
                prompt += f"- {violation}\n"
            prompt += "\nRe-translate and strictly follow the glossary mappings above."
        else:
            prompt = build_utterance_translation_prompt(
                zh_text, budget_ms, retry_level=retry,
                episode_context=episode_context,
                plot_overview=plot_overview,
                slang_glossary_text=slang_glossary_text,
                is_gemini=is_gemini,
            )
        en_text = translate_fn(prompt)

        if not en_text:
            continue

        # 注意：这里不再调用 clean_translation_output。
        # 占位符（<<NAME_x>> / <<NAME_x:...>>）、<sep> 等系统标记由 MT Phase 统一处理：
        # 1）先用 DictLoader / placeholder_to_name 替换人名占位符为英文名
        # 2）再在 MT Phase 里调用 clean_translation_output 移除系统标记
        en_est_ms = estimate_en_duration_ms(en_text)
        if en_est_ms <= budget_ms:
            return en_text, retry

        # 超限，继续重试
        if retry < max_retries - 1:
            warning(
                f"Translation too long: {en_est_ms:.0f}ms > {budget_ms:.0f}ms, "
                f"retrying (attempt {retry + 2}/{max_retries})"
            )

    # 所有重试都失败，返回最后一次结果（即使超限）
    return en_text, max_retries - 1


def resegment_utterance(
    en_text: str,
    utt_start_ms: int,
    utt_end_ms: int,
    target_wps: float = 2.5,  # 目标语速：words per second（经验值 2.5-3.0）
) -> List[Dict[str, Any]]:
    """
    在 utterance 时间窗口内重断句（不跨 utterance 边界）。

    核心原则（非常重要）：
    - 不使用 SSOT 的 cue.start/end（那是中文语音事实切片，不适用于英文）
    - 只使用 utterance 的 start/end 作为总时间预算
    - 基于语速模型重新分配时间轴

    时间分配算法（三步法）：
    1. 锁定 utterance 总时间窗：[utt_start_ms, utt_end_ms]（不可动的硬边界）
    2. 根据英文文本 + 目标语速，计算每个 segment 的理论发音时长
    3. 在 utt 时间窗内，按比例重新分配时间轴

    Args:
        en_text: 英文整句/整段（可能包含由 <sep> 诱导产生的标点）
        utt_start_ms: utterance 开始时间（毫秒，来自 SSOT）
        utt_end_ms: utterance 结束时间（毫秒，可能已扩展）
        target_wps: 目标语速（words per second，默认 2.5）

    Returns:
        segments[]（英文字幕段），每个包含：
        {
            "start_ms": int,
            "end_ms": int,
            "text": str,
        }
    """
    # 可配置参数
    no_resegment_threshold_ms = 1500  # 短句跳过 resegment
    min_segment_ms = 500             # 最短 segment 时长

    if not en_text or not en_text.strip():
        return []

    utt_duration_ms = utt_end_ms - utt_start_ms
    if utt_duration_ms <= 0:
        return []

    # 规则 A：短 utterance 直接输出一条 cue，不做重断句
    if utt_duration_ms < no_resegment_threshold_ms:
        return [{
            "start_ms": utt_start_ms,
            "end_ms": utt_end_ms,
            "text": en_text.strip(),
        }]

    # Step 1: 在英文文本中找自然切分点（标点、空格）
    # 优先级：标点（, . ? ! — ; :） > 空格 > 单词边界
    punctuation_pattern = r'[,\.\?!—;:]\s*'
    punctuation_positions = []
    for match in re.finditer(punctuation_pattern, en_text):
        pos = match.end()  # 标点后的位置（包含后续空格）
        punctuation_positions.append(pos)

    # Step 2: 按自然切分点切分文本（语义断句）
    text_segments = []
    current_pos = 0

    # 优先在标点处切分
    if punctuation_positions:
        for punc_pos in punctuation_positions:
            if punc_pos > current_pos:
                segment_text = en_text[current_pos:punc_pos].strip()
                # 过滤掉只包含标点符号的 segment
                if segment_text and not is_only_punctuation(segment_text):
                    text_segments.append(segment_text)
                current_pos = punc_pos

        # 添加最后一段
        if current_pos < len(en_text):
            segment_text = en_text[current_pos:].strip()
            # 过滤掉只包含标点符号的 segment
            if segment_text and not is_only_punctuation(segment_text):
                text_segments.append(segment_text)
    else:
        # 没有标点，按空格切分（但保持合理长度）
        words = en_text.split()
        if not words:
            return []

        # 简单策略：每 8-12 个词一段（或根据总时长动态调整）
        words_per_segment = max(8, min(12, int(len(words) / max(1, utt_duration_ms / 2000))))  # 每段约 2 秒

        for i in range(0, len(words), words_per_segment):
            segment_text = " ".join(words[i:i + words_per_segment])
            if segment_text:
                text_segments.append(segment_text)

    if not text_segments:
        # 如果无法切分，整个 utterance 作为一个 segment
        text_segments = [en_text.strip()]

    # Step 3: 计算每个 segment 的理论发音时长（基于语速模型）
    # 使用单词数估算（更准确）
    segment_estimates = []
    total_est_ms = 0.0

    for seg_text in text_segments:
        # 计算单词数（简单方法：空格数 + 1）
        word_count = len(seg_text.split())
        # 理论时长 = 单词数 / 目标语速（words per second）
        est_seconds = word_count / target_wps if target_wps > 0 else 0.5
        est_ms = est_seconds * 1000.0
        segment_estimates.append({
            "text": seg_text,
            "est_ms": est_ms,
            "word_count": word_count,
        })
        total_est_ms += est_ms

    # Step 4: 在 utt 时间窗内，按比例重新分配时间轴
    # scale = utt_duration / total_est
    if total_est_ms <= 0:
        # 如果估算失败，平均分配
        scale = utt_duration_ms / len(text_segments) if text_segments else 0
        segments = []
        current_time = utt_start_ms
        for seg_text in text_segments:
            segment_duration = scale
            segments.append({
                "start_ms": int(current_time),
                "end_ms": int(current_time + segment_duration),
                "text": seg_text,
            })
            current_time += segment_duration
        return segments

    scale = utt_duration_ms / total_est_ms

    segments = []
    current_time = utt_start_ms

    for seg_info in segment_estimates:
        # 映射到真实时间轴
        segment_duration = seg_info["est_ms"] * scale
        segment_start = int(current_time)
        segment_end = int(current_time + segment_duration)

        # 确保不超过 utterance 边界
        if segment_end > utt_end_ms:
            segment_end = utt_end_ms

        segments.append({
            "start_ms": segment_start,
            "end_ms": segment_end,
            "text": seg_info["text"],
        })

        current_time = segment_end

    # 确保最后一个 segment 的 end 正好是 utt_end_ms
    if segments:
        segments[-1]["end_ms"] = utt_end_ms

    # 规则 B：合并过短的 segment（向前合并，防止闪烁字幕）
    if len(segments) > 1:
        merged = [segments[0]]
        for seg in segments[1:]:
            seg_dur = seg["end_ms"] - seg["start_ms"]
            if seg_dur < min_segment_ms:
                # 向前合并：文本拼接，end_ms 取后者
                merged[-1]["text"] = merged[-1]["text"].rstrip() + " " + seg["text"]
                merged[-1]["end_ms"] = seg["end_ms"]
            else:
                merged.append(seg)
        # 检查第一个 segment 是否也过短（被后面的合并后可能不需要了，但防御性检查）
        if len(merged) > 1 and (merged[0]["end_ms"] - merged[0]["start_ms"]) < min_segment_ms:
            merged[1]["text"] = merged[0]["text"].rstrip() + " " + merged[1]["text"]
            merged[1]["start_ms"] = merged[0]["start_ms"]
            merged.pop(0)
        segments = merged

    return segments


def translate_utterance(
    utterance: Dict[str, Any],
    next_utterance: Optional[Dict[str, Any]],
    translate_fn: Callable[[str], str],
) -> Dict[str, Any]:
    """
    翻译单个 utterance（完整流程）。

    Args:
        utterance: Utterance 数据（来自 SSOT），包含：
            - utt_id: Utterance ID
            - start_ms: 开始时间（毫秒）
            - end_ms: 结束时间（毫秒）
            - speech_rate: {"zh_tps": float}
            - cues: [{"start_ms": int, "end_ms": int, "source": {"text": str}, ...}]
            - 注意：v1.3 已移除 cue_id，使用索引即可
        next_utterance: 下一个 utterance（用于计算不重叠扩展），None 表示没有下一句
        translate_fn: 翻译函数

    Returns:
        TranslationSet 条目：
        {
            "utt_id": str,
            "end_ms_final": int,  # 原始 end_ms 或延长后
            "segments": [  # 英文字幕段（时间轴基于语速模型重新分配）
                {
                    "start_ms": int,  # 基于语速模型在 utt 时间窗内重新计算
                    "end_ms": int,     # 不再使用 SSOT cue 的时间
                    "text": str,
                },
                ...
            ],
            "metrics": {
                "zh_tps": float,
                "k": float,
                "budget_ms": float,
                "en_est_ms": float,
                "extend_ms": float,
                "retries": int,
            },
        }
    """
    utt_id = utterance.get("utt_id", "")
    start_ms = utterance.get("start_ms", 0)
    end_ms = utterance.get("end_ms", 0)
    speech_rate = utterance.get("speech_rate", {})
    zh_tps = speech_rate.get("zh_tps", 0.0)
    cues = utterance.get("cues", [])

    # 1. 合并中文文本
    zh_merged = "".join(cue.get("source", {}).get("text", "") for cue in cues)
    if not zh_merged:
        return {
            "utt_id": utt_id,
            "end_ms_final": end_ms,
            "segments": [],
            "metrics": {
                "zh_tps": zh_tps,
                "k": 0.0,
                "budget_ms": 0.0,
                "en_est_ms": 0.0,
                "extend_ms": 0.0,
                "retries": 0,
            },
        }

    # 2. 计算 k 值和时间预算
    window_ms = end_ms - start_ms
    k = pick_k(zh_tps)
    budget_ms = window_ms * k

    # 3. 首次翻译
    en_text, retries = translate_utterance_with_retry(
        zh_text=zh_merged,
        budget_ms=budget_ms,
        translate_fn=translate_fn,
    )

    # 4. 估计英文时长
    en_est_ms = estimate_en_duration_ms(en_text)

    # 5. 如果超限，尝试扩展 end_time
    extend_ms = 0.0
    end_ms_final = end_ms
    budget_ms2 = budget_ms

    if en_est_ms > budget_ms:
        need_ms = en_est_ms - budget_ms
        next_utt_start_ms = next_utterance.get("start_ms") if next_utterance else None
        extend_ms = calculate_extend_ms(need_ms, end_ms, next_utt_start_ms)

        if extend_ms > 0:
            end_ms_final = int(end_ms + extend_ms)
            budget_ms2 = budget_ms + extend_ms

            # 如果扩展后仍超限，重翻译
            if en_est_ms > budget_ms2:
                en_text, retries = translate_utterance_with_retry(
                    zh_text=zh_merged,
                    budget_ms=budget_ms2,
                    translate_fn=translate_fn,
                )
                en_est_ms = estimate_en_duration_ms(en_text)

    # 6. 重断句（不跨 utterance 边界）
    # 重要：不使用 SSOT 的 cue 时间，只使用 utterance 时间预算
    segments = resegment_utterance(
        en_text=en_text,
        utt_start_ms=start_ms,
        utt_end_ms=end_ms_final,
        target_wps=2.5,  # 目标语速：words per second
    )

    return {
        "utt_id": utt_id,
        "end_ms_final": end_ms_final,
        "segments": segments,
        "metrics": {
            "zh_tps": zh_tps,
            "k": k,
            "budget_ms": budget_ms,
            "en_est_ms": en_est_ms,
            "extend_ms": extend_ms,
            "retries": retries,
        },
    }


def translate_utterances(
    utterances: List[Dict[str, Any]],
    translate_fn: Callable[[str], str],
) -> Dict[str, Any]:
    """
    翻译所有 utterances（utterance 粒度）。

    Args:
        utterances: Utterance 列表（来自 SSOT）
        translate_fn: 翻译函数

    Returns:
        TranslationSet：
        {
            "by_utt": {
                "utt_001": {
                    "end_ms_final": int,
                    "segments": [...],
                    "metrics": {...},
                },
                ...
            },
        }
    """
    results = {}

    info(f"Translating {len(utterances)} utterances (utterance-level)...")

    for i, utterance in enumerate(utterances):
        next_utterance = utterances[i + 1] if i + 1 < len(utterances) else None
        result = translate_utterance(utterance, next_utterance, translate_fn)
        results[result["utt_id"]] = result

    # 统计信息
    total_extend_ms = sum(r["metrics"]["extend_ms"] for r in results.values())
    total_retries = sum(r["metrics"]["retries"] for r in results.values())
    info(
        f"Translation completed: {len(results)} utterances, "
        f"total extend: {total_extend_ms:.0f}ms, total retries: {total_retries}"
    )

    return {
        "by_utt": results,
    }
