"""
ASR Model: 人工校准 IDE 的工作文件 SSOT v2

设计理念：
- 替代 asr.fix.json，成为人工校准的完整工作文件
- 支持 split/merge/时间轴微调等 IDE 操作
- 导出时转为 subtitle.model.json（v1.3），无缝接入下游 pipeline
- 时间单位统一用 int 毫秒（与全代码库 start_ms/end_ms 一致）

数据流：
  asr-result.json → import → dub.json → human calibration → export → subtitle.model.json
"""
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _gen_seg_id() -> str:
    """生成 segment ID: seg_ + 8-char hex"""
    return f"seg_{secrets.token_hex(4)}"



@dataclass
class AsrSegmentFlags:
    """
    段落标记（用于 IDE 标注和质检）。

    字段：
    - overlap: 与相邻段有时间重叠
    - needs_review: 需要人工审校
    """
    overlap: bool = False
    needs_review: bool = False



@dataclass
class AsrSegment:
    """
    ASR 校准段落。

    字段：
    - id: 唯一 ID（seg_ + 8-char hex），split 后不重排序
    - start_ms: 开始时间（毫秒）
    - end_ms: 结束时间（毫秒）
    - text: 文本内容（可编辑）
    - text_en: 英文翻译（可编辑，IDE 预留）
    - speaker: 说话人标识（可编辑）
    - emotion: 情绪标签（可编辑）
    - type: 段落类型（speech=对话, singing=唱歌）
    - gender: 性别（voice selection fallback）
    - tts_policy: TTS 策略（align 阶段写入）
    - flags: 段落标记
    """
    id: str
    start_ms: int
    end_ms: int
    text: str
    speaker: str
    text_en: str = ""
    emotion: str = "neutral"
    type: str = "speech"
    gender: Optional[str] = None
    tts_policy: Optional[dict] = None
    flags: AsrSegmentFlags = field(default_factory=AsrSegmentFlags)


@dataclass
class AsrMediaInfo:
    """
    媒体元信息。

    字段：
    - duration_ms: 总时长（毫秒）
    """
    duration_ms: int = 0


@dataclass
class AsrHistory:
    """
    编辑历史元信息。

    字段：
    - rev: 修订版本号（每次保存 +1）
    - created_at: 创建时间（ISO 格式）
    - updated_at: 最后更新时间（ISO 格式）
    """
    rev: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass
class AsrFingerprint:
    """
    内容指纹。

    字段：
    - algo: 算法（如 "sha256"）
    - value: 指纹值
    - scope: 计算范围（如 "segments"）
    """
    algo: str = "sha256"
    value: str = ""
    scope: str = "segments"


@dataclass
class AsrModel:
    """
    ASR Model: 人工校准 IDE 的工作文件 SSOT v2。

    字段：
    - schema: Schema 标识
    - media: 媒体元信息
    - segments: 校准段落列表
    - history: 编辑历史元信息
    - fingerprint: 内容指纹
    """
    schema: str = "asr.model.v2"
    media: AsrMediaInfo = field(default_factory=AsrMediaInfo)
    segments: List[AsrSegment] = field(default_factory=list)
    history: AsrHistory = field(default_factory=AsrHistory)
    fingerprint: AsrFingerprint = field(default_factory=AsrFingerprint)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于 JSON 输出）。"""
        return {
            "schema": self.schema,
            "media": {
                "duration_ms": self.media.duration_ms,
            },
            "segments": [
                {
                    "id": seg.id,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "text": seg.text,
                    "text_en": seg.text_en,
                    "speaker": seg.speaker,
                    "emotion": seg.emotion,
                    "type": seg.type,
                    "gender": seg.gender,
                    **({"tts_policy": seg.tts_policy} if seg.tts_policy else {}),
                    "flags": {
                        "overlap": seg.flags.overlap,
                        "needs_review": seg.flags.needs_review,
                    },
                }
                for seg in self.segments
            ],
            "history": {
                "rev": self.history.rev,
                "created_at": self.history.created_at,
                "updated_at": self.history.updated_at,
            },
            "fingerprint": {
                "algo": self.fingerprint.algo,
                "value": self.fingerprint.value,
                "scope": self.fingerprint.scope,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AsrModel":
        """从字典反序列化（向后兼容：忽略旧数据中的 speakers/emotions）。"""
        media_data = data.get("media", {})
        media = AsrMediaInfo(
            duration_ms=media_data.get("duration_ms", 0),
        )

        segments = []
        for seg_data in data.get("segments", []):
            flags_data = seg_data.get("flags", {})
            flags = AsrSegmentFlags(
                overlap=flags_data.get("overlap", False),
                needs_review=flags_data.get("needs_review", False),
            )

            segments.append(AsrSegment(
                id=seg_data.get("id", _gen_seg_id()),
                start_ms=seg_data.get("start_ms", 0),
                end_ms=seg_data.get("end_ms", 0),
                text=seg_data.get("text", ""),
                text_en=seg_data.get("text_en", ""),
                speaker=seg_data.get("speaker", "0"),
                emotion=seg_data.get("emotion", "neutral"),
                type=seg_data.get("type", "speech"),
                gender=seg_data.get("gender"),
                tts_policy=seg_data.get("tts_policy"),
                flags=flags,
            ))

        history_data = data.get("history", {})
        history = AsrHistory(
            rev=history_data.get("rev", 1),
            created_at=history_data.get("created_at", ""),
            updated_at=history_data.get("updated_at", ""),
        )

        fp_data = data.get("fingerprint", {})
        fingerprint = AsrFingerprint(
            algo=fp_data.get("algo", "sha256"),
            value=fp_data.get("value", ""),
            scope=fp_data.get("scope", "segments"),
        )

        return cls(
            schema="asr.model.v2",
            media=media,
            segments=segments,
            history=history,
            fingerprint=fingerprint,
        )

    def compute_fingerprint(self) -> str:
        """计算 segments 内容的 SHA256 指纹。"""
        # 只 hash segments 的核心字段（id, start_ms, end_ms, text, text_en, speaker, emotion）
        parts = []
        for seg in self.segments:
            parts.append(f"{seg.id}|{seg.start_ms}|{seg.end_ms}|{seg.text}|{seg.text_en}|{seg.speaker}|{seg.emotion}")
        content = "\n".join(parts)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def update_fingerprint(self) -> None:
        """重算并更新 fingerprint。"""
        self.fingerprint.value = self.compute_fingerprint()

    def bump_rev(self) -> None:
        """版本号 +1，更新 updated_at。"""
        self.history.rev += 1
        self.history.updated_at = datetime.now(timezone.utc).isoformat()

    def detect_overlaps(self) -> None:
        """检测相邻段落的时间重叠，设置 overlap flag。"""
        # 先清除所有 overlap 标记
        for seg in self.segments:
            seg.flags.overlap = False
        # 按 start_ms 排序检查
        sorted_segs = sorted(self.segments, key=lambda s: s.start_ms)
        for i in range(1, len(sorted_segs)):
            if sorted_segs[i].start_ms < sorted_segs[i - 1].end_ms:
                sorted_segs[i].flags.overlap = True
                sorted_segs[i - 1].flags.overlap = True
