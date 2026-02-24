"""
Pipeline core types: Artifact, PhaseResult, RunContext, etc.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

Status = Literal["pending", "running", "succeeded", "failed", "skipped"]


@dataclass(frozen=True)
class Artifact:
    """
    可被其他 Phase 消费的产物（只用于 runner / manifest）。

    注意：
    - relpath 始终是 workspace-relative 的相对路径
    - 绝对路径只在运行时由 runner 使用 (workspace / relpath)
    """

    key: str  # e.g. "subs.subtitle_model"
    relpath: str  # workspace-relative path, e.g. "subs/zh.srt"
    kind: Literal["json", "srt", "wav", "mp4", "txt", "bin", "dir"]
    fingerprint: str  # e.g. "sha256:..."
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorInfo:
    """错误信息。"""
    type: str
    message: str
    traceback: Optional[str] = None


@dataclass
class PhaseResult:
    """
    Phase 执行结果（不直接提交 Artifact，只声明哪些 outputs 成功产出）。

    - status: 仅表示被执行后的结果（成功 / 失败），不包含 skipped
    - outputs: 本次成功产出的 artifact keys（必须是 phase.provides() 的子集）
    """

    status: Literal["succeeded", "failed"]
    outputs: List[str] = field(default_factory=list)
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
class ResolvedOutputs:
    """
    Runner 预分配的输出路径（artifact_key -> absolute path）。
    
    Processor 可以写文件，但只能写到这些预分配的路径。
    Runner 负责路径分配、原子提交与 manifest 一致性。
    """
    paths: Dict[str, Path]  # artifact_key -> absolute Path
    
    def get(self, key: str) -> Path:
        """获取指定 artifact key 的输出路径。"""
        if key not in self.paths:
            raise KeyError(f"Output path not allocated for artifact key: {key}")
        return self.paths[key]


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
