"""
字典加载器（Dictionary Loader）

职责：
- 统一加载 dub/dict/ 目录下的所有字典文件
- 实现优先级：names.json（最高）→ slang.json → 其他
- 提供轻量校验接口（glossary violation check）
- 歧义术语（X万/X条）上下文感知：仅在麻将语境中注入/校验
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pikppo.utils.logger import info, warning

# ── 歧义牌名上下文检测 ─────────────────────────────────────

# 歧义后缀：万/条 既是麻将花色，又是数量单位/量词
_AMBIGUOUS_TILE_SUFFIXES = ("万", "条")

# 中文数字前缀（一到九）
_CN_DIGITS = frozenset("一二三四五六七八九")

# 无歧义麻将指标词：出现任何一个即可认定为麻将上下文
_MAHJONG_INDICATORS = (
    "筒", "饼",
    "碰", "杠", "暗杠", "明杠",
    "胡", "截胡", "屁胡", "地胡", "天胡",
    "自摸", "听牌", "点炮", "放炮", "钓",
)


def _is_ambiguous_tile_key(zh_term: str) -> bool:
    """判断 slang key 是否为歧义牌名（X万/X条，如「五万」「三条」）"""
    return (
        len(zh_term) == 2
        and zh_term[0] in _CN_DIGITS
        and zh_term[1] in _AMBIGUOUS_TILE_SUFFIXES
    )


def _has_mahjong_context(src_text: str) -> bool:
    """判断源文本是否含有无歧义的麻将指标词"""
    return any(ind in src_text for ind in _MAHJONG_INDICATORS)


class DictLoader:
    """字典加载器（统一管理所有字典）"""
    
    def __init__(self, dict_dir: Path):
        """
        初始化字典加载器。
        
        Args:
            dict_dir: dub/dict 目录路径
        """
        self.dict_dir = dict_dir
        self.names: Dict[str, str] = {}  # {中文名: 英文名}
        self.slang: Dict[str, str] = {}  # {中文术语: 英文翻译}
        self._load_all()
    
    def _load_all(self):
        """加载所有字典文件（按优先级顺序）"""
        # 1. 加载 names.json：{"中文名": "英文名"}
        names_path = self.dict_dir / "names.json"
        if names_path.exists():
            try:
                with open(names_path, "r", encoding="utf-8") as f:
                    self.names = json.load(f)
                info(f"Loaded names dictionary: {len(self.names)} entries from {names_path}")
            except Exception as e:
                warning(f"Failed to load names.json from {names_path}: {e}")
        else:
            info(f"Names dictionary not found: {names_path}, using empty dict")
        
        # 2. 加载 slang.json（次高优先级）
        slang_path = self.dict_dir / "slang.json"
        if slang_path.exists():
            try:
                with open(slang_path, "r", encoding="utf-8") as f:
                    self.slang = json.load(f)
                info(f"Loaded slang dictionary: {len(self.slang)} entries from {slang_path}")
            except Exception as e:
                warning(f"Failed to load slang.json from {slang_path}: {e}")
        else:
            info(f"Slang dictionary not found: {slang_path}, using empty dict")
    
    def resolve_name(self, src_name: str) -> Optional[str]:
        """解析人名，返回英文名或 None。"""
        return self.names.get(src_name)
    
    def has_name(self, src_name: str) -> bool:
        """检查人名是否在字典中"""
        return src_name in self.names
    
    def add_name(self, src_name: str, target: str) -> bool:
        """
        添加人名到字典（first-write-wins）。
        
        Args:
            src_name: 中文人名
            target: 英文名
        
        Returns:
            True 表示添加成功，False 表示已存在（不覆盖）
        """
        if src_name in self.names:
            return False  # 已存在，不覆盖
        
        self.names[src_name] = target
        return True
    
    def save_names(self):
        """保存 names.json"""
        names_path = self.dict_dir / "names.json"
        names_path.parent.mkdir(parents=True, exist_ok=True)
        with open(names_path, "w", encoding="utf-8") as f:
            json.dump(self.names, f, indent=2, ensure_ascii=False)
        info(f"Saved names dictionary: {len(self.names)} entries to {names_path}")
    
    def get_slang_glossary_text(self) -> str:
        """
        获取 slang 词表文本（用于 prompt，作为"必须遵守的术语表"）。

        Returns:
            格式化的词表字符串（用于 System Prompt）
        """
        if not self.slang:
            return ""

        lines = []
        for zh_term, en_translation in sorted(self.slang.items()):
            lines.append(f"{zh_term} -> {en_translation}")

        return "\n".join(lines)

    def get_glossary_hits(self, src_text: str) -> str:
        """
        获取与源文本匹配的 glossary 条目（按需注入，不污染无关句子）。

        歧义牌名（X万/X条）仅在源文本含有无歧义麻将指标词时才注入，
        避免「五万」在金钱语境下被错误映射为「5-Character」。

        Args:
            src_text: 当前 utterance 的中文源文本

        Returns:
            只包含命中条目的格式化字符串，无命中返回空串
        """
        if not self.slang:
            return ""

        mahjong_ctx = _has_mahjong_context(src_text)
        hits = []
        for zh_term, en_translation in sorted(self.slang.items()):
            if zh_term in src_text:
                if _is_ambiguous_tile_key(zh_term) and not mahjong_ctx:
                    continue
                hits.append(f"{zh_term} -> {en_translation}")

        return "\n".join(hits) if hits else ""
    
    def check_glossary_violation(self, src_text: str, out_text: str) -> List[str]:
        """
        检查 glossary 违反情况（轻量校验）。

        歧义牌名（X万/X条）仅在源文本含有无歧义麻将指标词时才校验。

        Args:
            src_text: 源文本（中文）
            out_text: 输出文本（英文）

        Returns:
            违反的术语列表（空列表表示无违反）

        规则：如果源文本包含 glossary key，但输出文本不包含对应 value，则视为违反。
        """
        violations = []
        out_lower = out_text.lower()
        mahjong_ctx = _has_mahjong_context(src_text)

        for zh_term, en_translation in self.slang.items():
            if zh_term in src_text:
                if _is_ambiguous_tile_key(zh_term) and not mahjong_ctx:
                    continue
                en_lower = en_translation.lower()
                if en_lower not in out_lower:
                    violations.append(f"{zh_term} -> {en_translation}")

        return violations
