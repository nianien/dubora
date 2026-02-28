"""
Reseg Phase: LLM 断句优化

职责：
- 读取 dub.json 和 asr-result.json
- 筛选过长段落，调用 LLM 断句
- 利用 word-level 时间戳精确切分
- 写回 dub.json（更新 segments）

不负责：
- 断句逻辑本身（由 processors/reseg.py 负责）
- LLM 客户端管理（复用 MT 的引擎选择逻辑）
"""
import json
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.utils.logger import info, warning, error


class ResegPhase(Phase):
    """LLM 断句优化 Phase。"""

    name = "reseg"
    version = "1.0.0"

    def requires(self) -> list[str]:
        """需要 dub.dub_manifest 和 asr.asr_result。"""
        return ["dub.dub_manifest", "asr.asr_result"]

    def provides(self) -> list[str]:
        """更新 dub.dub_manifest。"""
        return ["dub.dub_manifest"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Reseg Phase。

        流程：
        1. 检查 enabled 配置
        2. 读取 dub.json 和 asr-result.json
        3. 构造 translate_fn（复用 MT 引擎选择逻辑）
        4. 调用 processor
        5. 写回 dub.json
        """
        # 1. 读取 phase config
        phase_config = ctx.config.get("phases", {}).get("reseg", {})
        enabled = phase_config.get("enabled", ctx.config.get("reseg_enabled", True))

        if not enabled:
            info("Reseg phase disabled by config, skipping")
            return PhaseResult(
                status="succeeded",
                outputs=["dub.dub_manifest"],
                metrics={"skipped": True},
            )

        # 2. 读取 dub.json
        dub_artifact = inputs["dub.dub_manifest"]
        dub_path = Path(ctx.workspace) / dub_artifact.relpath

        if not dub_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"dub.json not found: {dub_path}",
                ),
            )

        from dubora.schema.asr_model import AsrModel
        with open(dub_path, "r", encoding="utf-8") as f:
            asr_model = AsrModel.from_dict(json.load(f))

        segments = asr_model.segments
        if not segments:
            info("No segments in dub.json, nothing to reseg")
            return PhaseResult(
                status="succeeded",
                outputs=["dub.dub_manifest"],
                metrics={"segments_count": 0},
            )

        # 3. 读取 asr-result.json
        asr_artifact = inputs["asr.asr_result"]
        asr_path = Path(ctx.workspace) / asr_artifact.relpath

        if not asr_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"ASR result not found: {asr_path}",
                ),
            )

        with open(asr_path, "r", encoding="utf-8") as f:
            asr_result = json.load(f)

        # 4. 构造 translate_fn（复用 MT 的引擎选择逻辑）
        engine = phase_config.get("engine")
        if not engine:
            model_name = phase_config.get("model", ctx.config.get("mt_model", ""))
            if model_name.startswith("gemini"):
                engine = "gemini"
            elif model_name.startswith("gpt") or model_name.startswith("o1"):
                engine = "openai"
            else:
                engine = ctx.config.get("mt_engine", "gemini")

        engine = engine.lower()
        is_gemini = (engine == "gemini")

        if is_gemini:
            from dubora.config.settings import get_gemini_key
            model = phase_config.get(
                "model",
                ctx.config.get("mt_model", ctx.config.get("gemini_model", "gemini-2.0-flash")),
            )
            api_key = phase_config.get("api_key") or get_gemini_key()
            if not api_key:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message="Gemini API key not found. Set GEMINI_API_KEY.",
                    ),
                )
            temperature = phase_config.get("temperature", 0.3)
            info(f"Reseg using Gemini engine: {model}")
        else:
            from dubora.config.settings import get_openai_key
            model = phase_config.get(
                "model",
                ctx.config.get("mt_model", ctx.config.get("openai_model", "gpt-4o-mini")),
            )
            api_key = phase_config.get("api_key") or get_openai_key()
            if not api_key:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message="OpenAI API key not found. Set OPENAI_API_KEY.",
                    ),
                )
            temperature = phase_config.get("temperature", 0.3)
            info(f"Reseg using OpenAI engine: {model}")

        try:
            from dubora.pipeline.processors.mt.time_aware_impl import create_translate_fn
            translate_fn = create_translate_fn(
                api_key=api_key,
                model=model,
                temperature=temperature,
            )
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=f"Failed to create translate function: {e}",
                ),
            )

        # 5. 读取 reseg 参数
        min_chars = phase_config.get(
            "min_chars", ctx.config.get("reseg_min_chars", 6),
        )
        max_chars_trigger = phase_config.get(
            "max_chars_trigger", ctx.config.get("reseg_max_chars_trigger", 25),
        )
        max_duration_trigger = phase_config.get(
            "max_duration_trigger", ctx.config.get("reseg_max_duration_trigger", 6000),
        )

        # 6. 调用 processor
        from dubora.pipeline.processors.reseg import run as reseg_run
        result = reseg_run(
            segments=segments,
            asr_result=asr_result,
            min_chars=min_chars,
            max_chars_trigger=max_chars_trigger,
            max_duration_trigger=max_duration_trigger,
            translate_fn=translate_fn,
        )

        # 7. 如果没有拆分发生，直接返回
        if result.split_count == 0:
            info("No segments were split, dub.json unchanged")
            return PhaseResult(
                status="succeeded",
                outputs=["dub.dub_manifest"],
                metrics={
                    "candidates_count": result.candidates_count,
                    "split_count": 0,
                    "new_segments_count": 0,
                },
            )

        # 8. 替换 segments，更新 dub.json
        asr_model.segments = result.new_segments
        asr_model.bump_rev()
        asr_model.update_fingerprint()

        model_path = outputs.get("dub.dub_manifest")
        model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(model_path, "w", encoding="utf-8") as f:
            json.dump(asr_model.to_dict(), f, indent=2, ensure_ascii=False)

        info(f"Saved updated dub.json to: {model_path}")

        return PhaseResult(
            status="succeeded",
            outputs=["dub.dub_manifest"],
            metrics={
                "candidates_count": result.candidates_count,
                "split_count": result.split_count,
                "new_segments_count": result.new_segments_count,
            },
        )
