"""
Pipeline core types: Artifact, PhaseResult, RunContext, etc.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set

Status = Literal["pending", "running", "succeeded", "failed", "skipped"]


@dataclass(frozen=True)
class Artifact:
    """可被其他 Phase 消费的产物。"""
    key: str                 # e.g. "subs.zh_segments"
    path: str                # workspace-relative path
    kind: str                # "json"|"srt"|"wav"|"mp4"
    fingerprint: str         # e.g. "sha256:..."
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorInfo:
    """错误信息。"""
    type: str
    message: str
    traceback: Optional[str] = None


@dataclass
class PhaseResult:
    """Phase 执行结果。"""
    status: Literal["succeeded", "failed"]
    artifacts: Dict[str, Artifact] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    error: Optional[ErrorInfo] = None


@dataclass
class RunContext:
    """运行上下文。"""
    job_id: str
    workspace: str
    config: Dict[str, Any]   # global + phases config


@dataclass(frozen=True)
class ExecutionPlan:
    """
    执行计划（目前是线性有序 phases，后续可扩展为 DAG）。
    """

    # Phase 名称按执行顺序排列（目前是线性 pipeline）
    phases: List[str]

    # 起始 / 结束 phase 名称（可选，主要用于记录和调试）
    from_phase: Optional[str] = None
    to_phase: Optional[str] = None

    # 需要强制重跑的 phase 名称集合
    force: Set[str] = field(default_factory=set)

    # 仅做 dry-run，不实际执行，只报告哪些 phase 会执行 / 跳过
    dry_run: bool = False


@dataclass
class PhaseRunRecord:
    """
    单个 phase 的执行记录（包含跳过 / 失败原因等）。
    """

    name: str
    status: Status
    # skipped / failed 等原因说明
    reason: Optional[str] = None
    # 该 phase 实际产出的 artifact keys（来自 manifest）
    artifacts: List[str] = field(default_factory=list)
    # metrics 快照（来自 manifest）
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunSummary:
    """
    一次 pipeline 运行的整体摘要。

    这是 Runner 的公共返回值，供 CLI / 上层系统消费。
    """

    status: Literal["succeeded", "failed"]
    # 按执行顺序记录所有 phase（包括 skipped）
    ran: List[PhaseRunRecord]
    # Manifest 中 artifacts 段的快照（key -> dict）
    artifacts: Dict[str, Any]
    # manifest 文件路径
    manifest_path: str
