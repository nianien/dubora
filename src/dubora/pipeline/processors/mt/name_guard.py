"""
Name Guard: 中文人名识别模块（字幕场景特化）

核心原则：
- 宁可多判人名，也不要漏判人名
- 使用多规则打分 + 阈值的方式
- 可配置，不依赖模型
"""
import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class NameGuardConfig:
    """Name Guard 配置 v2（定版）"""
    threshold: int = 100
    name_placeholder_format: str = "<<NAME_{index}>>"
    render_strategy: str = "pinyin"
    require_strong_signal: bool = True  # 必须命中至少一个强信号
    
    # 规则权重（v2：收紧权重，需要叠加才能过阈值）
    honorific_suffix_weight: int = 60  # v2: 降低到60，不能单独过阈值
    call_position_weight: int = 40  # v2: 降低到40
    repetition_weight: int = 40  # v2: 降低到40
    pos_exclusion_weight: int = -30
    length_structure_weight: int = 10
    
    # 规则模式
    honorific_suffix_patterns: List[str] = None
    call_position_checks: List[str] = None
    repetition_min_occurrences: int = 2
    pos_exclusion_words: List[str] = None
    length_preferred: List[int] = None
    length_exclude_endings: List[str] = None
    
    # 强排除规则（闸门 1）
    interjections: List[str] = None
    action_verbs: List[str] = None
    pronouns: List[str] = None
    numbers_quantifiers: List[str] = None
    particles: List[str] = None  # v2: 新增语气助词
    kinship_roles: List[str] = None
    
    # 安全约束
    lao_prefix_max_len: int = 1  # v2: 老X 的前缀最大长度
    
    # 特殊处理
    known_names: List[str] = None
    exclude_names: List[str] = None
    
    def __post_init__(self):
        if self.honorific_suffix_patterns is None:
            self.honorific_suffix_patterns = []
        if self.call_position_checks is None:
            self.call_position_checks = []
        if self.pos_exclusion_words is None:
            self.pos_exclusion_words = []
        if self.length_preferred is None:
            self.length_preferred = [1, 2, 3]
        if self.length_exclude_endings is None:
            self.length_exclude_endings = []
        if self.interjections is None:
            self.interjections = []
        if self.action_verbs is None:
            self.action_verbs = []
        if self.pronouns is None:
            self.pronouns = []
        if self.numbers_quantifiers is None:
            self.numbers_quantifiers = []
        if self.particles is None:
            self.particles = []
        if self.kinship_roles is None:
            self.kinship_roles = []
        if self.known_names is None:
            self.known_names = []
        if self.exclude_names is None:
            self.exclude_names = []


def load_config(config_path: Optional[Path] = None) -> NameGuardConfig:
    """
    加载 Name Guard 配置。
    
    Args:
        config_path: 配置文件路径，如果为 None 则使用默认路径
    
    Returns:
        NameGuardConfig 对象
    """
    if config_path is None:
        # 默认配置文件路径
        config_path = Path(__file__).parent / "name_guard.yaml"
    
    if not config_path.exists():
        # 如果配置文件不存在，使用默认配置
        return NameGuardConfig()
    
    with open(config_path, "r", encoding="utf-8") as f:
        config_dict = yaml.safe_load(f)
    
    strategy = config_dict.get("strategy", {})
    rules = config_dict.get("rules", {})
    strong_exclusion = config_dict.get("strong_exclusion", {})
    special = config_dict.get("special", {})
    constraints = config_dict.get("constraints", {})
    
    # 提取规则配置
    honorific_suffix = rules.get("honorific_suffix", {})
    call_position = rules.get("call_position", {})
    repetition = rules.get("repetition", {})
    pos_exclusion = rules.get("pos_exclusion", {})
    length_structure = rules.get("length_structure", {})
    
    return NameGuardConfig(
        threshold=strategy.get("threshold", 100),
        name_placeholder_format=strategy.get("name_placeholder_format", "<<NAME_{index}>>"),
        render_strategy=strategy.get("render_strategy", "pinyin"),
        require_strong_signal=strategy.get("require_strong_signal", True),
        
        # v2: 收紧权重，需要叠加才能过阈值
        honorific_suffix_weight=honorific_suffix.get("weight", 60) if honorific_suffix.get("enabled", True) else 0,
        call_position_weight=call_position.get("weight", 40) if call_position.get("enabled", True) else 0,
        repetition_weight=repetition.get("weight", 40) if repetition.get("enabled", True) else 0,
        pos_exclusion_weight=pos_exclusion.get("weight", -30) if pos_exclusion.get("enabled", True) else 0,
        length_structure_weight=length_structure.get("weight", 10) if length_structure.get("enabled", True) else 0,
        
        honorific_suffix_patterns=honorific_suffix.get("patterns", []),
        call_position_checks=call_position.get("positions", call_position.get("check_positions", [])),  # 兼容旧字段名
        repetition_min_occurrences=repetition.get("min_occurrences", 2),
        pos_exclusion_words=pos_exclusion.get("exclude_words", []),
        length_preferred=length_structure.get("preferred_lengths", [1, 2, 3]),
        length_exclude_endings=length_structure.get("exclude_endings", []),
        
        interjections=strong_exclusion.get("interjections", []),
        action_verbs=strong_exclusion.get("action_verbs", []),
        pronouns=strong_exclusion.get("pronouns", []),
        numbers_quantifiers=strong_exclusion.get("numbers_quantifiers", []),
        particles=strong_exclusion.get("particles", []),  # v2: 新增语气助词
        kinship_roles=strong_exclusion.get("kinship_roles", []),
        
        lao_prefix_max_len=constraints.get("lao_prefix_max_len", 1),  # v2: 老X 的前缀最大长度
        
        known_names=special.get("known_names", []),
        exclude_names=special.get("exclude_names", []),
    )


class NameGuard:
    """人名识别器（打分器）"""
    
    def __init__(self, config: Optional[NameGuardConfig] = None):
        """
        初始化 Name Guard。
        
        Args:
            config: 配置对象，如果为 None 则从默认配置文件加载
        """
        self.config = config or load_config()
    
    def score_word(
        self,
        word: str,
        context: str,
        position: int = 0,
        is_utterance_start: bool = False,
        is_after_sep: bool = False,
    ) -> Tuple[int, Dict[str, int]]:
        """
        对单个词进行人名打分。
        
        Args:
            word: 待评分的词
            context: 上下文文本（整个 utterance）
            position: 词在上下文中的位置
            is_utterance_start: 是否在 utterance 开头
            is_after_sep: 是否在 <sep> 后
        
        Returns:
            (总分, 各规则得分详情)
        """
        scores = {}
        total_score = 0
        
        # 规则 1: 称呼后缀规则
        if self.config.honorific_suffix_weight > 0:
            for pattern in self.config.honorific_suffix_patterns:
                if word.endswith(pattern):
                    scores["honorific_suffix"] = self.config.honorific_suffix_weight
                    total_score += self.config.honorific_suffix_weight
                    break
        
        # 规则 2: 呼唤位规则
        if self.config.call_position_weight > 0:
            if is_utterance_start or is_after_sep:
                scores["call_position"] = self.config.call_position_weight
                total_score += self.config.call_position_weight
        
        # 规则 3: 重复出现规则（v2: 权重降低到40）
        if self.config.repetition_weight > 0:
            occurrences = context.count(word)
            if occurrences >= self.config.repetition_min_occurrences:
                scores["repetition"] = self.config.repetition_weight
                total_score += self.config.repetition_weight
        
        # 规则 4: 词性排除规则（负权重）
        if self.config.pos_exclusion_weight < 0:
            if word in self.config.pos_exclusion_words:
                scores["pos_exclusion"] = self.config.pos_exclusion_weight
                total_score += self.config.pos_exclusion_weight
        
        # 规则 5: 长度与结构规则
        if self.config.length_structure_weight > 0:
            word_len = len(word)
            if word_len in self.config.length_preferred:
                # 检查结尾
                exclude_ending = False
                for ending in self.config.length_exclude_endings:
                    if word.endswith(ending):
                        exclude_ending = True
                        break
                
                if not exclude_ending:
                    scores["length_structure"] = self.config.length_structure_weight
                    total_score += self.config.length_structure_weight
        
        return total_score, scores
    
    def is_strongly_excluded(self, word: str) -> bool:
        """
        闸门 1：强排除检查（命中直接判定"不是人名"）。
        
        Args:
            word: 待检查的词
        
        Returns:
            True 表示应该排除（不是人名）
        """
        # 1. 语气词（单字和多字）
        if word in self.config.interjections:
            return True
        
        # 1.1 检查是否包含语气词（如"哈哈哈哈哈"包含"哈哈"）
        for interjection in self.config.interjections:
            if interjection in word and len(interjection) >= 2:
                return True
        
        # 2. 语气助词（v2: 新增）
        if word in self.config.particles:
            return True
        
        # 3. 祈使/动作短语（以动词开头且长度≥2）
        if len(word) >= 2 and any(word.startswith(verb) for verb in self.config.action_verbs):
            return True
        
        # 4. 含明显语法功能词（代词）
        if any(pronoun in word for pronoun in self.config.pronouns):
            return True
        
        # 5. 数字/量词短语
        if any(num in word for num in self.config.numbers_quantifiers):
            return True
        
        # 6. 亲属称呼/角色（v2: 这些不算专名，彻底排除）
        if word in self.config.kinship_roles:
            return True
        
        return False
    
    def has_strong_signal(self, word: str, context: str, is_utterance_start: bool, is_after_sep: bool, has_suffix: bool = False) -> bool:
        """
        闸门 2：检查是否满足强信号（必须命中至少一个强信号才算人名）。
        
        v2 强信号（收紧）：
        1. 如果是从带后缀的词中提取的名字部分，直接通过（如"平安哥" -> "平安"）
        2. 后缀称呼 + 名字结构（X哥/X姐/老X）- 但权重只有60，需要叠加其他信号
        3. 词在 utterance 中重复出现 ≥2 且长度 2-3（权重40）
        4. 出现在呼唤位（utterance 开头或 <sep> 后首 token）且长度 2-3（权重40）
        
        Args:
            word: 待检查的词
            context: 上下文文本
            is_utterance_start: 是否在 utterance 开头
            is_after_sep: 是否在 <sep> 后
            has_suffix: 是否是从带后缀的词中提取的名字部分（如从"平安哥"中提取的"平安"）
        
        Returns:
            True 表示满足强信号
        """
        word_len = len(word)
        
        # 强信号 1: 如果是从带后缀的词中提取的名字部分，直接通过（如"平安哥" -> "平安"）
        if has_suffix:
            return True
        
        # 强信号 2: 后缀称呼 + 名字结构（检查完整词）
        # v2: 权重只有60，不能单独过阈值，但可以作为强信号之一
        for pattern in self.config.honorific_suffix_patterns:
            if word.endswith(pattern) and word_len >= 2:
                # 检查前缀部分（名字部分）长度合理
                name_part = word[:-len(pattern)]
                # v2: "老X" 的安全约束
                if pattern == "老" and len(name_part) > self.config.lao_prefix_max_len:
                    continue
                if 1 <= len(name_part) <= 3:
                    return True  # 有后缀称呼，算强信号
        
        # 强信号 3: 重复出现 ≥2 且长度 2-3（v2: 权重40）
        if 2 <= word_len <= 3:
            occurrences = context.count(word)
            if occurrences >= self.config.repetition_min_occurrences:
                return True
        
        # 强信号 4: 呼唤位且长度 2-3（v2: 权重40）
        if 2 <= word_len <= 3:
            if is_utterance_start or is_after_sep:
                return True
        
        return False
    
    def is_name(
        self,
        word: str,
        context: str,
        position: int = 0,
        is_utterance_start: bool = False,
        is_after_sep: bool = False,
        skip_strong_signal_check: bool = False,
    ) -> Tuple[bool, int, Dict[str, int]]:
        """
        判断一个词是否应该当人名处理。
        
        Args:
            word: 待判断的词
            context: 上下文文本
            position: 词在上下文中的位置
            is_utterance_start: 是否在 utterance 开头
            is_after_sep: 是否在 <sep> 后
            skip_strong_signal_check: 是否跳过强信号检查（用于从带后缀的词中提取的名字部分）
        
        Returns:
            (是否为人名, 总分, 各规则得分详情)
        """
        # 白名单：直接判定为人名
        if word in self.config.known_names:
            return True, 999, {"known_name": 999}
        
        # 闸门 1：强排除（直接判定"不是人名"）
        if self.is_strongly_excluded(word):
            return False, -999, {"strong_exclusion": -999}
        
        # 黑名单：直接排除（但白名单优先级更高）
        if word in self.config.exclude_names and word not in self.config.known_names:
            return False, -999, {"exclude_name": -999}
        
        # 闸门 2：如果启用强信号模式，必须满足至少一个强信号
        # 如果 skip_strong_signal_check=True，则跳过此检查（用于从带后缀的词中提取的名字部分）
        if self.config.require_strong_signal and not skip_strong_signal_check:
            if not self.has_strong_signal(word, context, is_utterance_start, is_after_sep, has_suffix=False):
                return False, 0, {"no_strong_signal": 0}
        
        # 打分
        score, scores = self.score_word(
            word=word,
            context=context,
            position=position,
            is_utterance_start=is_utterance_start,
            is_after_sep=is_after_sep,
        )
        
        # 阈值判定
        is_name = score >= self.config.threshold
        
        return is_name, score, scores
    
    def extract_and_replace_names(
        self,
        text: str,
        sep_marker: str = " <sep> ",
    ) -> Tuple[str, Dict[str, str]]:
        """
        从文本中提取人名并替换为占位符。
        
        闸门 0：候选只从 cue 文本本体取（不做整句滑窗）。
        
        Args:
            text: 输入文本（可能包含 <sep> 标记，每个 <sep> 分隔的部分对应一个 cue）
            sep_marker: <sep> 标记字符串
        
        Returns:
            (替换后的文本, name_map: {placeholder: original_name})
        """
        # 分割文本（保留 <sep> 位置信息）
        # 每个 part 对应一个 cue 的文本
        parts = text.split(sep_marker)
        
        name_map = {}  # {placeholder: original_name}
        name_to_placeholder = {}  # {name: placeholder} - 用于去重：同一个名字使用同一个占位符
        name_index = 0
        replaced_parts = []
        
        for part_idx, part in enumerate(parts):
            # 判断是否在 utterance 开头或 <sep> 后
            is_utterance_start = (part_idx == 0)
            is_after_sep = (part_idx > 0)
            
            # 闸门 0：候选只从 cue 文本本体取（不做整句滑窗）
            # 只提取 cue 文本中的完整词（1-3 字），不做滑窗截取
            
            # 更精确的词提取：优先匹配更长的人名，避免误匹配
            # 策略：
            # 1. 先匹配带后缀的完整人名（如"平安哥"）
            # 2. 再匹配不带后缀的人名（如"平安"）
            # 3. 避免匹配到句子中间的非人名片段
            
            replaced_part = part
            processed_ranges = []  # 记录已处理的字符范围 (start, end)
            
            # 提取所有候选词，按优先级排序
            candidates = []
            
            # 第一轮：匹配带后缀的完整词（3-4 字，如"平安哥"、"王师傅"）
            # 如果识别为人名，只保护名字部分，不保护后缀
            for length in [4, 3]:
                for match in re.finditer(r'[\u4e00-\u9fff]{' + str(length) + '}', part):
                    word = match.group()
                    start_pos = match.start()
                    end_pos = match.end()
                    
                    # 检查是否带后缀，并提取名字部分
                    name_part = None
                    suffix_part = None
                    for pattern in self.config.honorific_suffix_patterns:
                        if word.endswith(pattern):
                            name_part = word[:-len(pattern)]  # 名字部分（如"平安"、"王"）
                            suffix_part = pattern  # 后缀部分（如"哥"、"师傅"）
                            break
                    
                    if not name_part:
                        continue
                    
                    # 对于从带后缀的词中提取的名字部分，跳过强信号检查
                    # 因为"平安哥"中的"平安"、"王师傅"中的"王"都是明确的人名
                    # 判断名字部分是否为人名（只检查名字部分，不检查后缀，跳过强信号检查）
                    is_name, score, scores = self.is_name(
                        word=name_part,
                        context=text,
                        position=start_pos,
                        is_utterance_start=is_utterance_start and (start_pos == 0),
                        is_after_sep=is_after_sep and (start_pos == 0),
                        skip_strong_signal_check=True,  # 跳过强信号检查（因为是从带后缀的词中提取的）
                    )
                    
                    if is_name:
                        # 只保护名字部分，不保护后缀
                        # 替换范围：只替换名字部分（name_part），保留后缀（suffix_part）
                        name_end_pos = start_pos + len(name_part)
                        candidates.append((name_part, start_pos, name_end_pos, score, True, part_idx))  # True = 带后缀，但只保护名字部分，part_idx 用于跨 part 去重
                        # 记录已处理的范围（避免第二轮重复匹配）
                        # 注意：记录完整词的范围（start_pos, end_pos），防止第二轮匹配到"平安哥"中的"平安"
                        processed_ranges.append((start_pos, end_pos))
            
            # 第二轮：匹配不带后缀的人名（1-3 字）
            # 只匹配在词边界位置的词（避免匹配到句子中间）
            # 注意：这里不检查 processed_ranges，因为 candidates 还没有收集完，processed_ranges 会在后面统一检查
            for length in [3, 2, 1]:
                for match in re.finditer(r'[\u4e00-\u9fff]{' + str(length) + '}', part):
                    word = match.group()
                    start_pos = match.start()
                    end_pos = match.end()
                    
                    # 检查是否与 candidates 中已有的重叠（避免重复添加）
                    overlaps_with_candidates = any(
                        start_pos < existing_end and end_pos > existing_start
                        for _, existing_start, existing_end, _, _, _ in candidates
                    )
                    if overlaps_with_candidates:
                        continue
                    
                    # 检查是否带后缀（已在第一轮处理）
                    has_suffix = any(word.endswith(pattern) for pattern in self.config.honorific_suffix_patterns)
                    if has_suffix:
                        continue
                    
                    # 边界检查：只匹配在词边界的词
                    # 1. 在开头（呼唤位）
                    # 2. 在标点后
                    # 3. 在空格后
                    is_at_start = (start_pos == 0)
                    char_before = part[start_pos - 1] if start_pos > 0 else None
                    char_after = part[end_pos] if end_pos < len(part) else None
                    
                    # 检查是否在词边界（使用字符集合，避免转义问题）
                    boundary_chars = set('，。！？、；： \t\n')
                    is_at_boundary = (
                        is_at_start or
                        (char_before and char_before in boundary_chars) or
                        (char_after and char_after in boundary_chars)
                    )
                    
                    # 白名单名字不受边界限制（用户/LLM 已确认是人名）
                    is_known = word in self.config.known_names

                    # 如果不在边界、不是呼唤位、也不在白名单，跳过
                    if not is_at_boundary and not is_at_start and not is_known:
                        continue
                    
                    # 判断是否在呼唤位
                    is_after_sep_pos = is_after_sep and is_at_start
                    
                    # 判断是否为人名
                    is_name, score, scores = self.is_name(
                        word=word,
                        context=text,
                        position=start_pos,
                        is_utterance_start=is_utterance_start and is_at_start,
                        is_after_sep=is_after_sep_pos,
                    )
                    
                    if is_name:
                        candidates.append((word, start_pos, end_pos, score, False, part_idx))  # False = 不带后缀，part_idx 用于跨 part 去重
            
            # 按优先级排序：带后缀优先，然后按分数和位置
            candidates.sort(key=lambda x: (-x[4], -x[3], x[1]))  # (带后缀, 分数, 位置)
            
            # 先收集所有要替换的位置和占位符（去重）
            replacements = []  # [(start_pos, end_pos, placeholder)]
            
            for word, start_pos, end_pos, score, has_suffix, part_idx_candidate in candidates:
                # 去重：如果同一个名字已经在其他 part 中出现过，使用同一个占位符
                if word in name_to_placeholder:
                    placeholder = name_to_placeholder[word]
                else:
                    # 生成新占位符
                    placeholder = self.config.name_placeholder_format.format(index=name_index)
                    name_map[placeholder] = word
                    name_to_placeholder[word] = placeholder
                    name_index += 1
                
                replacements.append((start_pos, end_pos, placeholder))
                
                # 记录已处理的范围（在原始文本中的位置）
                processed_ranges.append((start_pos, end_pos))
            
            # 从后往前替换（避免位置偏移问题）
            # 按位置从大到小排序
            replacements.sort(key=lambda x: x[0], reverse=True)
            
            for start_pos, end_pos, placeholder in replacements:
                # 从后往前替换，不需要调整位置偏移
                replaced_part = (
                    replaced_part[:start_pos] +
                    placeholder +
                    replaced_part[end_pos:]
                )
            
            replaced_parts.append(replaced_part)
        
        # 重新组合（恢复 <sep>）
        replaced_text = sep_marker.join(replaced_parts)
        
        return replaced_text, name_map
