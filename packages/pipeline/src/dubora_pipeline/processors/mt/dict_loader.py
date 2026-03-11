"""
字典加载器（Dictionary Loader）— DB-backed

职责：
- 从 DbStore 加载 names / slang 字典
- 提供轻量校验接口（glossary violation check）
- 歧义术语（X万/X条）上下文感知：仅在麻将语境中注入/校验
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from dubora_core.utils.logger import info

if TYPE_CHECKING:
    from dubora_core.store import DbStore

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
    """字典加载器（DB-backed）"""

    def __init__(self, store: DbStore, drama_id: int):
        self.store = store
        self.drama_id = drama_id
        self.names: Dict[str, str] = store.get_dict_map(drama_id, 'name')
        self.slang: Dict[str, str] = store.get_dict_map(drama_id, 'slang')
        info(f"DictLoader: loaded {len(self.names)} names, {len(self.slang)} slang from DB (drama_id={drama_id})")

    def resolve_name(self, src_name: str) -> Optional[str]:
        """解析人名，返回英文名或 None。"""
        return self.names.get(src_name)

    def has_name(self, src_name: str) -> bool:
        """检查人名是否在字典中"""
        return src_name in self.names

    def add_name(self, src_name: str, target: str) -> bool:
        """添加人名到字典（first-write-wins）。写入内存 + DB。"""
        if src_name in self.names:
            return False
        self.names[src_name] = target
        self.store.upsert_dict_entry(self.drama_id, 'name', src_name, target)
        return True

    def save_names(self):
        """No-op: names are persisted on add_name()."""
        pass

    def get_glossary_hits(self, src_text: str) -> str:
        """获取与源文本匹配的 glossary 条目（按需注入）。"""
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
        """检查 glossary 违反情况（轻量校验）。"""
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
