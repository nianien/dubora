"""
ASR Phase: 语音识别

先跑 doubao，检查空洞总时长。超过 10s 再并发跑 tencent + fish 补充。
增量执行：已有 asr-{model}.json 的模型跳过。

产出：
  asr-{model}.json: 各模型原始结果
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_core.utils.file_store import get_gcs_store, get_tos_store
from dubora_core.utils.logger import info, error as log_error

# 模型 → 对象存储（doubao/tencent 用 TOS，gemini 用 GCS，openai/fish 用本地文件）
_MODEL_STORE = {
    "doubao": "tos",
    "tencent": "tos",
    "gemini": "gcs",
    "openai": "local",
    "fish": "local",
}


class ASRPhase(Phase):
    """语音识别 Phase。"""

    name = "asr"
    version = "4.0.0"

    def requires(self) -> list[str]:
        return ["extract.audio"]

    def provides(self) -> list[str]:
        return ["asr.doubao", "asr.tencent", "asr.fish"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        audio_key = "extract.vocals" if "extract.vocals" in inputs else "extract.audio"
        audio_artifact = inputs[audio_key]
        audio_path = Path(ctx.workspace) / audio_artifact.relpath

        if not audio_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(type="FileNotFoundError", message=f"Audio file not found: {audio_path}"),
            )
        if audio_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(type="RuntimeError", message=f"Audio file is empty: {audio_path}"),
            )

        force = ctx.config.get("force", False)

        info(f"Audio: {audio_path.name} ({audio_path.stat().st_size / 1024 / 1024:.1f}MB)")

        # episode 信息（热词 + blob key）
        ep = None
        hotwords = None
        if ctx.store and ctx.episode_id:
            ep = ctx.store.get_episode(ctx.episode_id)
            if ep:
                names = ctx.store.get_dict_map(ep["drama_id"], "name")
                if names:
                    hotwords = list(names.keys())
                    info(f"Loaded {len(hotwords)} hotwords from glossary")

        drama_name = ep["drama_name"] if ep else "unknown"
        ep_number = ep["number"] if ep else "0"
        blob_key = f"dramas/{drama_name}/asr/{ep_number}.wav"

        try:
            # ── 第一步：跑 doubao ──
            doubao_path = Path(ctx.workspace) / "asr-doubao.json"
            if not force and doubao_path.exists() and doubao_path.stat().st_size > 0:
                info("ASR: skip doubao (asr-doubao.json exists)")
                doubao_result = None
            else:
                info("ASR: running doubao")
                tos = get_tos_store()
                tos.write_file(audio_path, blob_key)
                tos_url = tos.get_url(blob_key, expires=36000)
                task_fn = _make_task("doubao", {"tos": tos_url}, audio_path, ctx.config, hotwords)
                doubao_result = task_fn()

            # 保存 doubao 结果
            actual_outputs = []
            metrics = {}
            errors = {}
            if doubao_result is not None:
                _save_result("doubao", doubao_result, outputs, ctx.workspace, actual_outputs, metrics)

            # ── 第二步：检查空洞，决定是否跑补充模型 ──
            extra_models = _check_gaps_and_get_extras(doubao_path)

            if extra_models:
                # 增量跳过已有结果的
                if not force:
                    extra_models = [
                        m for m in extra_models
                        if not (Path(ctx.workspace) / f"asr-{m}.json").exists()
                        or (Path(ctx.workspace) / f"asr-{m}.json").stat().st_size == 0
                    ]

                if extra_models:
                    info(f"ASR: running extra models={extra_models}")
                    # 按需上传
                    stores_needed = {_MODEL_STORE.get(m, "tos") for m in extra_models}
                    urls = {}
                    if "tos" in stores_needed:
                        tos = get_tos_store()
                        tos.write_file(audio_path, blob_key)
                        urls["tos"] = tos.get_url(blob_key, expires=36000)
                    if "gcs" in stores_needed:
                        gcs = get_gcs_store()
                        gcs.write_file(audio_path, blob_key)
                        urls["gcs"] = gcs.get_url(blob_key, expires=36000)

                    tasks = {m: _make_task(m, urls, audio_path, ctx.config, hotwords) for m in extra_models}

                    if len(tasks) == 1:
                        model = extra_models[0]
                        try:
                            result = tasks[model]()
                            _save_result(model, result, outputs, ctx.workspace, actual_outputs, metrics)
                        except Exception as e:
                            errors[model] = e
                    else:
                        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                            future_map = {pool.submit(fn): m for m, fn in tasks.items()}
                            for future in as_completed(future_map):
                                model = future_map[future]
                                try:
                                    result = future.result()
                                    _save_result(model, result, outputs, ctx.workspace, actual_outputs, metrics)
                                except Exception as e:
                                    errors[model] = e

                    for model, err in errors.items():
                        log_error(f"ASR {model} failed: {err}")

            if not actual_outputs and not any(
                (Path(ctx.workspace) / f"asr-{m}.json").exists() for m in ["doubao", "tencent", "fish"]
            ):
                msg = "; ".join(f"{m}: {e}" for m, e in errors.items())
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(type="RuntimeError", message=f"All ASR models failed: {msg}"),
                )

            if errors:
                metrics["failed_models"] = list(errors.keys())

            return PhaseResult(
                status="succeeded",
                outputs=actual_outputs,
                metrics=metrics,
            )

        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(type=type(e).__name__, message=str(e)),
            )


_GAP_THRESHOLD_MS = 10000  # 空洞总时长超过 10s 才调补充模型


def _check_gaps_and_get_extras(doubao_path: Path) -> list[str]:
    """检查 doubao 结果的空洞，决定是否需要补充模型。"""
    if not doubao_path.exists():
        return ["tencent", "fish"]

    with open(doubao_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    total_ms = data.get("audio_info", {}).get("duration", 0)
    utts = data.get("result", {}).get("utterances", [])
    if not utts or not total_ms:
        return ["tencent", "fish"]

    from dubora_pipeline.processors.asr.fusion import find_gaps
    gaps = find_gaps(
        [{"start_ms": u["start_time"], "end_ms": u["end_time"]} for u in utts],
        total_ms,
    )
    gap_total = sum(e - s for s, e in gaps)
    info(f"ASR: doubao gaps={len(gaps)}, total_gap={gap_total}ms ({gap_total/1000:.1f}s)")

    if gap_total > _GAP_THRESHOLD_MS:
        info(f"ASR: gap {gap_total}ms > {_GAP_THRESHOLD_MS}ms, need tencent+fish")
        return ["tencent", "fish"]
    else:
        info(f"ASR: gap {gap_total}ms <= {_GAP_THRESHOLD_MS}ms, skip tencent+fish")
        return []


def _save_result(model: str, result, outputs: ResolvedOutputs, workspace, actual_outputs: list, metrics: dict):
    """保存单个模型结果到文件。"""
    artifact_key = f"asr.{model}"
    out_path = outputs.get(artifact_key)
    if out_path is None:
        out_path = Path(workspace) / f"asr-{model}.json"

    if isinstance(result, tuple):
        raw, utts = result
        data = raw
        metrics[f"{model}_segments"] = len(utts)
    elif isinstance(result, dict):
        data = result
        segments = result.get("segments") or result.get("utterances") or []
        metrics[f"{model}_segments"] = len(segments)
    else:
        data = {"utterances": result}
        metrics[f"{model}_segments"] = len(result)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    info(f"Saved {artifact_key}: {Path(out_path).name}")
    actual_outputs.append(artifact_key)


def _make_task(model: str, urls: dict, audio_path: Path, config: dict, hotwords=None):
    """为指定模型创建调用闭包。"""
    if model == "doubao":
        from dubora_pipeline.processors.asr.impl import transcribe
        return lambda: transcribe(
            audio_url=urls["tos"],
            preset="asr_spk_semantic",
            hotwords=hotwords,
        )

    if model == "tencent":
        from dubora_pipeline.processors.asr.tencent import transcribe_tencent
        return lambda: transcribe_tencent(audio_url=urls["tos"])

    if model == "gemini":
        from dubora_pipeline.models.gemini.asr_client import transcribe_with_gemini
        from dubora_core.config.settings import get_gemini_key
        gemini_model = config["asr_gemini_model"]
        gemini_key = get_gemini_key()
        return lambda: transcribe_with_gemini(
            urls["gcs"],
            api_key=gemini_key,
            model_name=gemini_model,
        )

    if model == "openai":
        from dubora_pipeline.processors.asr.openai import transcribe_openai
        return lambda: transcribe_openai(audio_path)

    if model == "fish":
        import os
        from fish_audio_sdk import Session, ASRRequest
        key = os.getenv("FISH_API_KEY")
        if not key:
            raise RuntimeError("需要 FISH_API_KEY")
        session = Session(apikey=key)

        def _fish_transcribe():
            with open(audio_path, "rb") as f:
                result = session.asr(ASRRequest(audio=f.read(), ignore_timestamps=False))
            return result.model_dump()

        return _fish_transcribe

    raise ValueError(f"Unknown ASR model: {model}")
