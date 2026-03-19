"""
ASR Phase: 语音识别（只做识别，不负责字幕后处理）

职责：
- 读取音频文件
- 上传到 TOS（如果需要）
- 调用 ASR API
- 保存原始 ASR 响应（asr.asr_result，SSOT）

产出：
- asr.asr_result：SSOT（原始响应，包含完整语义信息，emotion/gender/score/degree）

不负责：
- 字幕后处理（由 Parse Phase 负责）
- 切句策略（由 Parse Phase 负责）
"""
import json
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_pipeline.processors.asr import run as asr_run
from dubora_core.utils.file_store import get_tos_store
from dubora_core.utils.logger import info


class ASRPhase(Phase):
    """语音识别 Phase（只做识别，不负责字幕后处理）。"""

    name = "asr"
    version = "1.0.0"

    def requires(self) -> list[str]:
        """需要 extract.audio 或 extract.vocals（由 _LazyPhase 根据 config 动态决定）。"""
        return ["extract.audio"]

    def provides(self) -> list[str]:
        """生成 asr.asr_result（SSOT）。"""
        return ["asr.asr_result"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 ASR Phase。

        流程：
        1. 读取音频文件
        2. 上传到 TOS（如果需要）
        3. 调用 ASR API
        4. 保存原始 ASR 响应
        """
        # 获取输入音频（根据 asr_use_vocals 配置，可能是 extract.vocals 或 extract.audio）
        audio_artifact = inputs.get("extract.vocals") or inputs["extract.audio"]
        audio_path = Path(ctx.workspace) / audio_artifact.relpath

        if not audio_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Audio file not found: {audio_path}",
                ),
            )

        if audio_path.stat().st_size == 0:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"Audio file is empty: {audio_path}",
                ),
            )

        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("asr", {})
        preset = phase_config.get("preset", ctx.config.get("doubao_asr_preset", "asr_spk_semantic"))
        # 热词：从 DB glossary 表加载人名
        hotwords = None
        if ctx.store and ctx.episode_id:
            ep = ctx.store.get_episode(ctx.episode_id)
            if ep:
                names = ctx.store.get_dict_map(ep["drama_id"], "name")
                if names:
                    hotwords = list(names.keys())
                    info(f"Loaded {len(hotwords)} hotwords from glossary")

        info(f"ASR strategy: preset={preset}")
        info(f"Audio file: {audio_path.name} (size: {audio_path.stat().st_size / 1024 / 1024:.2f} MB)")

        try:
            # 1. 获取音频 URL（上传到 TOS 如果需要）
            audio_url = ctx.config.get("doubao_audio_url")
            if not audio_url:
                # 如果是 URL 直接使用，否则上传到 TOS
                audio_path_str = str(audio_path)
                if audio_path_str.startswith(("http://", "https://")):
                    audio_url = audio_path_str
                else:
                    tos = get_tos_store()
                    ep = ctx.store.get_episode(ctx.episode_id) if ctx.store and ctx.episode_id else None
                    drama_name = ep["drama_name"] if ep else "unknown"
                    ep_number = ep["number"] if ep else "0"
                    blob_key = f"dramas/{drama_name}/asr/{ep_number}.wav"
                    tos.write_file(audio_path, blob_key)
                    audio_url = tos.get_url(blob_key, expires=36000)

            # 2. 调用 Processor 层进行 ASR
            result = asr_run(
                audio_url=audio_url,
                preset=preset,
                hotwords=hotwords,
            )

            # 从 ProcessorResult 提取数据
            raw_response = result.data["raw_response"]
            utterances = result.data["utterances"]

            if not utterances:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message="ASR produced no utterances",
                    ),
                )

            info(f"ASR succeeded ({len(utterances)} utterances)")

            # 3. Phase 层负责文件 IO：写入到 runner 预分配的 outputs.paths
            # 只保存 raw response（SSOT，包含完整语义信息）
            raw_response_path = outputs.get("asr.asr_result")
            raw_response_path.parent.mkdir(parents=True, exist_ok=True)
            with open(raw_response_path, "w", encoding="utf-8") as f:
                json.dump(raw_response, f, indent=2, ensure_ascii=False)

            info(f"Saved raw ASR response (SSOT) to: {raw_response_path}")

            # 返回 PhaseResult
            return PhaseResult(
                status="succeeded",
                outputs=[
                    "asr.asr_result",  # SSOT（原始响应，不可编辑）
                ],
                metrics={
                    "utterances_count": len(utterances),
                },
            )

        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )
