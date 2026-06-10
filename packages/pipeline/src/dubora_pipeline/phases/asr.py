"""ASR Phase: 单源豆包 + Gemini scene context 辅助。

工作流：
  1. 用 Gemini 听一遍音频 → 生成业务场景描述，缓存到 asr-context.json
  2. 把场景描述注入豆包 ASR 的 corpus.context dialog_ctx
  3. 调豆包 (Seed-ASR 2.0) 转写音频 → asr-doubao.json

实测对短剧 / 广告 / ASMR 等领域特定音频，
Gemini scene context 能把豆包 ASR 准确率从 ~50% 拉到 ~92%。

历史 - 之前曾有 doubao + tencent + fish + gemini 多源融合方案，已下线（2026-06）。
"""
import json
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_core.utils.file_store import get_gcs_store, get_tos_store
from dubora_core.utils.logger import info, error as log_error


class ASRPhase(Phase):
    """语音识别 Phase（豆包单源 + Gemini scene context）。"""

    name = "asr"
    version = "5.0.0"

    def requires(self) -> list[str]:
        return ["extract.audio"]

    def provides(self) -> list[str]:
        return ["asr.doubao"]

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
        # blob_key 基于 audio_path 实际文件名派生，确保 vocals 模式下用 {ep}-vocals.wav 而非 {ep}.wav
        blob_key = f"dramas/{drama_name}/asr/{audio_path.name}"

        # 1. 增量复用：asr-doubao.json 已存在则直接 return，避免浪费 Gemini scene context 调用
        primary_path = Path(ctx.workspace) / "asr-doubao.json"
        if not force and primary_path.exists() and primary_path.stat().st_size > 0:
            info("ASR: skip doubao (asr-doubao.json exists)")
            return PhaseResult(
                status="succeeded",
                outputs=["asr.doubao"],
                metrics={"reused": True},
            )

        # 2. 生成业务场景上下文（Gemini 听音频，结果缓存复用；失败降级到空 context）
        scene_description = _ensure_scene_description(ctx, audio_path)
        if scene_description:
            info(f"ASR scene context: {scene_description[:100]}...")

        # 3. 跑豆包 ASR
        try:
            tos = get_tos_store()
            tos.write_file(audio_path, blob_key)
            audio_url = tos.get_url(blob_key, expires=36000)

            from dubora_pipeline.processors.asr.impl import transcribe
            raw, utts = transcribe(
                audio_url=audio_url,
                preset="asr_spk_semantic",
                hotwords=hotwords,
                scene_description=scene_description,
            )

            # manifest 已注册 asr.doubao artifact，outputs.get 拿到的就是预解析路径
            out_path = outputs.get("asr.doubao")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
            info(f"Saved asr.doubao: {out_path.name} ({len(utts)} utterances)")

            return PhaseResult(
                status="succeeded",
                outputs=["asr.doubao"],
                metrics={"doubao_segments": len(utts)},
            )
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(type=type(e).__name__, message=str(e)),
            )


def _ensure_scene_description(ctx: RunContext, audio_path: Path) -> str:
    """返回业务场景上下文文本（用于豆包 corpus.context dialog_ctx）。

    缓存：workspace/asr-context.json
    生成：本地 wav inline bytes 喂 Gemini 音频分析。失败返回 ""
    （豆包仍可单跑，只是准确率下降）。
    """
    cache_path = Path(ctx.workspace) / "asr-context.json"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            text = (data.get("scene_description") or "").strip()
            if text:
                info(f"ASR scene context: reusing cache {cache_path.name}")
                return text
        except (json.JSONDecodeError, OSError) as e:
            info(f"ASR scene context: cache unreadable ({e}), regenerating")

    from dubora_core.config.settings import get_gemini_key
    from dubora_pipeline.models.gemini.scene_context_client import generate_scene_context

    api_key = get_gemini_key()
    if not api_key:
        info("ASR scene context: GEMINI_API_KEY missing, skip")
        return ""

    model_name = ctx.config.get("gemini_model", "gemini-3.5-flash")
    # Gemini 调用任何步失败都降级到空 context，不阻塞主 ASR 流程 —— 豆包可以无
    # scene_description 单跑（只是准确率下降）。改 inline bytes 喂 Gemini，不再
    # 走 GCS 上传 + signed URL：AI Studio key 模式下 Gemini from_uri 不支持任意
    # HTTPS URL 抓取，会报 INVALID_ARGUMENT "Cannot fetch content"。
    try:
        info(f"ASR scene context: generating via Gemini ({model_name})...")
        text = generate_scene_context(
            audio_path, api_key=api_key, model_name=model_name, mime_type="audio/wav",
        )
    except Exception as e:
        log_error(f"ASR scene context generation failed: {e}")
        return ""

    try:
        cache_path.write_text(
            json.dumps({"scene_description": text, "model": model_name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        info(f"ASR scene context: cached to {cache_path.name}")
    except OSError as e:
        log_error(f"Failed to cache scene context: {e}")
    return text
