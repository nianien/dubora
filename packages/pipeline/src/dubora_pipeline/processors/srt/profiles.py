"""
字幕后处理策略配置（axis-first 系列）

职责：
- 定义字幕后处理参数（切分、合并、长度限制等）
- 纯常量，不掺任何 ASR 字段

禁止：
- ❌ ASR 预设字段（resource_id, vad_segment 等）
- ❌ API 调用
- ❌ 算法实现
"""
from typing import Any, Dict

_AXIS_BASE = dict(
    allow_merge=False,  # 轴优先：禁止合并
    hard_gap_ms=800,  # 强停顿必切
    hard_punc="。！？；",  # 强标点必切
    soft_punc="，",  # 软标点可切
)

POSTPROFILES: Dict[str, Dict[str, Any]] = {
    # 轴优先（最碎，lip-sync 最强）
    "axis": dict(
        description="Axis-first subtitle mode (lip-sync oriented, no merge)",
        soft_gap_ms=400,
        max_dur_ms=2800,
        max_chars=18,
        pad_end_ms=60,
        **_AXIS_BASE,
    ),

    # 轴优先（通用，推荐默认）
    "axis_default": dict(
        description="Axis-first but slightly smoother (gap=500ms)",
        soft_gap_ms=500,
        max_dur_ms=3000,
        max_chars=20,
        pad_end_ms=60,
        **_AXIS_BASE,
    ),

    # 轴优先（稍顺，不那么碎）
    "axis_soft": dict(
        description="Axis-first but less fragmented (gap=600ms)",
        soft_gap_ms=600,
        max_dur_ms=3200,
        max_chars=22,
        pad_end_ms=80,
        **_AXIS_BASE,
    ),
}
