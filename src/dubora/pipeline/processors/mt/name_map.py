"""
NameMap: 增量字典（Incremental Lexicon）系统 - 极简版

核心哲学：
- 名字不是翻译结果，是第一次出现时被"命名"的设定
- 设定一旦成立，全项目服从
- First-write-wins：第一次翻译出来的人名，假定一定会被用到，一旦出现并翻译，整个项目内永不替换

数据结构（极简）：
{
  "阿强": {
    "target": "Qiang",
    "style": "given-name",
    "first_seen": "ep01",
    "source": "llm",
    "alternatives": []
  }
}
"""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any


@dataclass
class NameEntry:
    """NameMap 中的单个条目（极简版）"""
    target: str  # 唯一生效的英文名（永不变）
    style: str  # 翻译风格（用于 debug / 人工检查）
    first_seen: str  # 首次出现的上下文 ID（排查问题用，不参与逻辑）
    source: str  # 来源：llm | rule | manual
    alternatives: List[str] = field(default_factory=list)  # 只记录，不使用


class NameMap:
    """增量字典管理器（极简版：first-write-wins）"""
    
    def __init__(self, map_path: Path):
        """
        初始化 NameMap。
        
        Args:
            map_path: NameMap 文件路径（JSON）
        """
        self.map_path = map_path
        self._map: Dict[str, NameEntry] = {}
        self._load()
    
    def _load(self):
        """加载 NameMap"""
        if self.map_path.exists():
            try:
                with open(self.map_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._map = {
                        src_name: NameEntry(**entry_data)
                        for src_name, entry_data in data.items()
                    }
            except Exception as e:
                # 如果加载失败，使用空字典
                self._map = {}
        else:
            self._map = {}
    
    def save(self):
        """保存 NameMap"""
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            src_name: asdict(entry)
            for src_name, entry in self._map.items()
        }
        with open(self.map_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def get(self, src_name: str) -> Optional[NameEntry]:
        """
        获取人名翻译（强约束）。
        
        Args:
            src_name: 源语言人名
        
        Returns:
            NameEntry 或 None（如果不存在）
        """
        return self._map.get(src_name)
    
    def has(self, src_name: str) -> bool:
        """检查人名是否已存在"""
        return src_name in self._map
    
    def get_target(self, src_name: str) -> Optional[str]:
        """
        获取目标语言翻译（快捷方法）。
        
        Args:
            src_name: 源语言人名
        
        Returns:
            目标语言翻译或 None
        """
        entry = self.get(src_name)
        return entry.target if entry else None
    
    def add(
        self,
        src_name: str,
        target: str,
        style: str,
        first_seen: str,
        source: str = "llm",
    ) -> bool:
        """
        添加人名映射（first-write-wins：如果已存在，不覆盖）。
        
        Args:
            src_name: 源语言人名
            target: 目标语言翻译
            style: 翻译风格
            first_seen: 首次出现的上下文 ID
            source: 来源（llm | rule | manual）
        
        Returns:
            True 表示添加成功，False 表示已存在（不覆盖）
        """
        if src_name in self._map:
            # 已存在，不覆盖（first-write-wins）
            return False
        
        # 首次出现，立刻写入并锁死
        self._map[src_name] = NameEntry(
            target=target,
            style=style,
            first_seen=first_seen,
            source=source,
            alternatives=[],
        )
        return True
    
    def record_alternative(
        self,
        src_name: str,
        alternative_target: str,
    ):
        """
        记录备选翻译（如果后面模型"翻得更好"，记录但不使用）。
        
        Args:
            src_name: 源语言人名
            alternative_target: 备选翻译
        """
        if src_name in self._map:
            entry = self._map[src_name]
            if alternative_target != entry.target and alternative_target not in entry.alternatives:
                entry.alternatives.append(alternative_target)
    
    def get_missing_names(self, src_names: List[str]) -> List[str]:
        """
        获取缺失的人名列表（需要 LLM 补全）。
        
        Args:
            src_names: 源语言人名列表
        
        Returns:
            缺失的人名列表
        """
        return [name for name in src_names if not self.has(name)]
    
    def batch_get_targets(self, src_names: List[str]) -> Dict[str, str]:
        """
        批量获取目标语言翻译。
        
        Args:
            src_names: 源语言人名列表
        
        Returns:
            {src_name: target} 映射（只包含已存在的）
        """
        return {
            src_name: entry.target
            for src_name in src_names
            if (entry := self.get(src_name)) is not None
        }
    
    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """转换为字典（用于序列化）"""
        return {
            src_name: asdict(entry)
            for src_name, entry in self._map.items()
        }
