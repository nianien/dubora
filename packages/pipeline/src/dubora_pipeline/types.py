"""
Pipeline core types: Artifact, PhaseResult, RunContext, etc.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

Status = Literal["pending", "running", "succeeded", "failed", "skipped"]

GateStatus = Literal["pending", "awaiting", "passed"]


@dataclass(frozen=True)
class Artifact:
    """
    Phase 间传递的文件引用（仅用于 runner → phase 的 inputs 传递）。

    注意：
    - relpath 始终是 workspace-relative 的相对路径
    - 绝对路径在运行时由 runner 使用 (workspace / relpath)
    - 路径由 resolve_artifact_path() 动态计算，不存 DB
    """

    key: str  # e.g. "extract.audio"
    relpath: str  # workspace-relative path, e.g. "input/5.wav"
    kind: str = "bin"  # e.g. "json", "wav", "mp4"
    fingerprint: str = ""  # unused, kept for compatibility


@dataclass
class ErrorInfo:
    """错误信息。"""
    type: str
    message: str
    traceback: Optional[str] = None


@dataclass
class PhaseResult:
    """
    Phase 执行结果。

    - status: 成功 / 失败
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
    store: Any = None        # DbStore (optional, for utterance CRUD)
    episode_id: Optional[int] = None  # DB episode id


@dataclass(frozen=True)
class ResolvedOutputs:
    """
    Runner 预分配的输出路径（artifact_key -> absolute path）。
    """
    paths: Dict[str, Path]  # artifact_key -> absolute Path

    def get(self, key: str) -> Path:
        """获取指定 artifact key 的输出路径。"""
        if key not in self.paths:
            raise KeyError(f"Output path not allocated for artifact key: {key}")
        return self.paths[key]
