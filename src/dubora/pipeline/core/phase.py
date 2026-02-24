"""
Phase 接口定义：统一 Phase 契约
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List

from dubora.pipeline.core.types import RunContext, PhaseResult, Artifact, ResolvedOutputs


class Phase(ABC):
    """Phase 抽象基类。"""
    
    name: str
    version: str  # 逻辑/契约变更必须 bump，用于 invalidation

    @abstractmethod
    def requires(self) -> List[str]:
        """
        返回该 Phase 需要的 artifact keys（从 manifest.artifacts 获取）。
        
        Returns:
            artifact keys 列表，例如 ["subs.subtitle_model"]
        """
        pass

    @abstractmethod
    def provides(self) -> List[str]:
        """
        返回该 Phase 将生成的 artifact keys。
        
        Returns:
            artifact keys 列表，例如 ["translate.context", "subs.subtitle_model"]
        """
        pass

    @abstractmethod
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Phase 逻辑。
        
        Args:
            ctx: 运行上下文
            inputs: 已解析的 artifacts（key -> Artifact）
            outputs: Runner 预分配的输出路径（artifact_key -> absolute Path）
        
        Returns:
            PhaseResult，包含生成的 artifacts
            - artifacts 的 path 应该使用 outputs.paths 中的路径
            - runner 会负责原子提交与 manifest 一致性
        """
        pass
