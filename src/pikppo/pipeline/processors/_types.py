"""
Processors 统一类型定义

所有 processor 必须返回 ProcessorResult，保持接口一致性。

边界定义：
- Processor 可以写文件，但只能写到 runner/phase 预分配的输出路径
- Runner/Phase 负责输出路径分配、原子提交与 manifest 一致性
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProcessorResult:
    """
    Processor 执行结果（统一返回格式）。
    
    边界约束：
    - outputs: 已成功写入的 artifact keys（必须是 runner 分配 outputs 的子集）
    - data: 可选的结构化数据，供 Phase 使用（例如 utterances / segments / context 等）
    - metrics/warnings/error: 可选，便于 Phase 透传到 PhaseResult
    """
    outputs: List[str] = field(default_factory=list)  # artifact keys, not paths
    data: Optional[Dict[str, Any]] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
