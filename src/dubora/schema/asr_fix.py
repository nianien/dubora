"""
ASR Fix Schema: 人工校准层

核心理念：
- asr-result.json 是 ASR 原始输出（不可编辑），包含 word 级时间轴
- asr.fix.json 是人工校准层（可编辑），由 ASR phase 自动全量生成
- 人工可编辑 speaker、text、emotion
- SUB 阶段同时读取两个文件：word 时间轴来自 asr-result，校准信息来自 asr.fix

设计要点：
- idx 为 int，指向原始 asr-result.json 中 utterance 的数组下标
- 拆分时允许重复 idx：两条 idx=62 表示原始第 62 条被拆为两段
  - 按数组顺序分配原始 words
- 只含可编辑字段：idx、speaker、text、emotion
- 可选 start_ms/end_ms：ASR 漏识别时人工指定时间轴，有则直接用，无则从 word 推算
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


def _parse_time_to_ms(val: Union[int, float, str, None]) -> Optional[int]:
    """将时间值解析为毫秒。

    支持格式：
    - int/float: 直接当毫秒
    - "01:23": MM:SS → 83000ms
    - "01:23.5": MM:SS.frac → 83500ms
    - "1:01:23": H:MM:SS → 3683000ms
    - "1:01:23.5": H:MM:SS.frac → 3683500ms
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val:
        return None

    # 纯数字 → 毫秒
    try:
        return int(val)
    except ValueError:
        pass

    # H:MM:SS.frac / MM:SS.frac
    m = re.match(r'^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?$', val)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    frac_str = m.group(4) or "0"
    frac_ms = int(frac_str.ljust(3, '0')[:3])  # 取前3位补齐为毫秒
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + frac_ms


@dataclass
class AsrFixUtterance:
    """
    ASR Fix 中的单条 utterance。

    字段：
    - idx: 原始 asr-result.json 中 utterance 的数组下标（拆分时可重复）
    - speaker: 说话人标识（可编辑）
    - text: 文本内容（可编辑）
    - emotion: 情绪标签（可编辑）
    - start_ms: 可选，人工指定开始时间（ASR 漏识别时使用）
    - end_ms: 可选，人工指定结束时间（ASR 漏识别时使用）
    """
    idx: int
    speaker: str
    text: str
    emotion: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None

    @property
    def has_manual_time(self) -> bool:
        """是否有人工指定的时间轴"""
        return self.start_ms is not None and self.end_ms is not None


@dataclass
class AsrFix:
    """
    ASR Fix（人工校准层）。

    schema.name = "asr.fix"
    schema.version = "1.0"
    """
    schema_name: str = "asr.fix"
    schema_version: str = "1.0"
    utterances: List[AsrFixUtterance] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        utterances_list = []
        for u in self.utterances:
            entry: Dict[str, Any] = {
                "idx": u.idx,
                "speaker": u.speaker,
                "text": u.text,
                "emotion": u.emotion,
            }
            # 只在有手动时间轴时输出，保持自动生成的文件简洁
            if u.start_ms is not None:
                entry["start_ms"] = u.start_ms
            if u.end_ms is not None:
                entry["end_ms"] = u.end_ms
            utterances_list.append(entry)
        return {
            "schema": {
                "name": self.schema_name,
                "version": self.schema_version,
            },
            "utterances": utterances_list,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AsrFix":
        schema = data.get("schema", {})
        utterances = []
        for u in data.get("utterances", []):
            start_ms = _parse_time_to_ms(u.get("start") or u.get("start_ms"))
            end_ms = _parse_time_to_ms(u.get("end") or u.get("end_ms"))
            # 有 start_ms/end_ms 就是插入，idx 强制 -1（防止复制粘贴忘改 idx）
            if start_ms is not None and end_ms is not None:
                idx = -1
            else:
                idx = u.get("idx", 0)
            utterances.append(AsrFixUtterance(
                idx=int(idx),
                speaker=str(u.get("speaker", "0")),
                text=str(u.get("text", "")),
                emotion=u.get("emotion"),
                start_ms=start_ms,
                end_ms=end_ms,
            ))
        return cls(
            schema_name=schema.get("name", "asr.fix"),
            schema_version=schema.get("version", "1.0"),
            utterances=utterances,
        )

    @classmethod
    def from_raw_response(cls, raw_response: Dict[str, Any]) -> "AsrFix":
        """从 ASR raw response 自动全量生成 AsrFix（初始状态，未经人工校准）。"""
        result = raw_response.get("result") or {}
        raw_utterances = result.get("utterances") or []

        utterances = []
        for idx, raw_utt in enumerate(raw_utterances):
            additions = raw_utt.get("additions") or {}
            utterances.append(AsrFixUtterance(
                idx=idx,
                speaker=str(additions.get("speaker", "0")),
                text=str(raw_utt.get("text", "")).strip(),
                emotion=additions.get("emotion") or "neutral",
            ))

        return cls(utterances=utterances)

    def group_by_idx(self) -> Dict[int, List[AsrFixUtterance]]:
        """按 idx 分组，用于处理拆分的 utterance。

        返回 {idx: [utterances...]}，每组内保持数组顺序。
        未拆分：{62: [utt]}，拆分后：{62: [utt_a, utt_b]}
        """
        groups: Dict[int, List[AsrFixUtterance]] = {}
        for u in self.utterances:
            if u.idx not in groups:
                groups[u.idx] = []
            groups[u.idx].append(u)
        return groups
