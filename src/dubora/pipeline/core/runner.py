"""
PhaseRunner: 执行协议 + should_run 决策

Artifact 路径由 resolve_artifact_path() 动态计算，不依赖 DB 存储。
should_run 仅检查 task 状态 + phase version + 输入文件存在性。
"""
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from dubora.pipeline.core.types import Artifact, ErrorInfo, RunContext, ResolvedOutputs
from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.manifest import resolve_artifact_path, now_iso
from dubora.utils.logger import info, warning, error


class PhaseRunner:
    """Phase 执行器。"""

    def __init__(self, manifest, workspace: Path):
        self.manifest = manifest
        self.workspace = workspace

    def should_run(
        self,
        phase: Phase,
        *,
        force: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        判断 phase 是否需要运行。

        检查顺序：
        1. force 标记
        2. task 状态是否为 succeeded
        3. phase.version 是否变化
        4. 输入文件是否存在
        5. 输出文件是否存在

        Returns:
            (should_run, reason) 元组
        """
        if force:
            return True, "forced"

        phase_data = self.manifest.get_phase_data(phase.name)

        if phase_data is None:
            return True, "not in manifest"

        if phase_data.get("version") != phase.version:
            return True, f"version changed: {phase_data.get('version')} -> {phase.version}"

        # 输入文件是否存在
        for key in phase.requires():
            path = resolve_artifact_path(key, self.workspace)
            if not path.exists():
                return True, f"required input '{key}' file not found: {path}"

        # 输出文件是否存在
        for key in phase.provides():
            path = resolve_artifact_path(key, self.workspace)
            if not path.exists():
                return True, f"output '{key}' file not found: {path}"

        if phase_data.get("status") != "succeeded":
            return True, f"status is {phase_data.get('status')} (expected 'succeeded')"

        return False, "all checks passed"

    def resolve_inputs(
        self,
        phase: Phase,
    ) -> Dict[str, Artifact]:
        """
        解析 phase 需要的 inputs（路径动态计算）。

        Returns:
            key -> Artifact 字典
        """
        required_keys = phase.requires()
        artifacts = {}

        for key in required_keys:
            abs_path = resolve_artifact_path(key, self.workspace)
            relpath = str(abs_path.relative_to(self.workspace))
            artifacts[key] = Artifact(
                key=key,
                relpath=relpath,
                kind=self._guess_kind(abs_path),
                fingerprint="",
            )

        return artifacts

    def allocate_outputs(
        self,
        phase: Phase,
    ) -> ResolvedOutputs:
        """为 phase 分配输出路径。"""
        provided_keys = phase.provides()
        paths = {}

        for key in provided_keys:
            absolute_path = resolve_artifact_path(key, self.workspace)
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            paths[key] = absolute_path

        return ResolvedOutputs(paths=paths)

    def _guess_kind(self, path: Path) -> str:
        """根据文件路径猜测 artifact kind。"""
        if path.is_dir():
            return "dir"
        suffix = path.suffix.lower()
        kind_map = {
            ".json": "json",
            ".srt": "srt",
            ".wav": "wav",
            ".mp4": "mp4",
            ".mp3": "mp3",
        }
        return kind_map.get(suffix, "bin")

    def run_phase(
        self,
        phase: Phase,
        ctx: RunContext,
        *,
        force: bool = False,
    ) -> tuple[bool, str | None]:
        """
        运行 phase。

        Returns:
            是否成功
        """
        should_run, reason = self.should_run(phase, force=force, config=ctx.config)

        if not should_run:
            info(f"Phase '{phase.name}' skipped: {reason}")
            phase_data = self.manifest.get_phase_data(phase.name)
            current_status = phase_data.get("status") if phase_data else None
            skip_status = "skipped" if current_status != "succeeded" else "succeeded"

            self.manifest.update_phase(
                phase.name,
                version=phase.version,
                status=skip_status,
                finished_at=now_iso(),
                skipped=True,
            )
            self.manifest.save()
            return True, None

        # 解析 inputs
        inputs = self.resolve_inputs(phase)

        # 标记为 running
        self.manifest.update_phase(
            phase.name,
            version=phase.version,
            status="running",
            started_at=now_iso(),
            requires=phase.requires(),
            provides=phase.provides(),
            skipped=False,
        )
        self.manifest.save()

        # 分配输出路径
        outputs = self.allocate_outputs(phase)

        # 执行 phase
        try:
            info(f"Running phase '{phase.name}'...")
            result = phase.run(ctx, inputs, outputs)

            if result.status == "succeeded":
                # 验证输出文件已写入
                for key in result.outputs:
                    if key not in outputs.paths:
                        raise ValueError(
                            f"Phase '{phase.name}' declared output '{key}' "
                            "which is not in phase.provides() / allocated outputs"
                        )
                    abs_path = outputs.paths[key]
                    if not abs_path.exists():
                        raise FileNotFoundError(
                            f"Phase '{phase.name}' did not write output file: {abs_path} "
                            f"(artifact key: {key})"
                        )

                self.manifest.update_phase(
                    phase.name,
                    version=phase.version,
                    status="succeeded",
                    finished_at=now_iso(),
                    metrics=result.metrics,
                    warnings=result.warnings,
                )
                self.manifest.save()

                info(f"Phase '{phase.name}' succeeded")
                return True, None
            else:
                self.manifest.update_phase(
                    phase.name,
                    version=phase.version,
                    status="failed",
                    finished_at=now_iso(),
                    error=result.error,
                    warnings=result.warnings,
                )
                self.manifest.save()

                err_msg = result.error.message if result.error else "unknown error"
                error(f"Phase '{phase.name}' failed: {err_msg}")
                return False, err_msg

        except Exception as e:
            error(f"Phase '{phase.name}' raised exception: {e}")
            error(f"Traceback:\n{traceback.format_exc()}")
            self.manifest.update_phase(
                phase.name,
                version=phase.version,
                status="failed",
                finished_at=now_iso(),
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                    traceback=traceback.format_exc(),
                ),
            )
            self.manifest.save()
            return False, str(e)
