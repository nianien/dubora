"""
TTS Phase: 语音合成（Timeline-First Architecture）

输入: tts.manifest.json (from Align phase)
输出:
  - tts.segments_dir: Per-segment WAV files
  - tts.report: TTS synthesis report (JSON)
  - tts.voice_assignment: Speaker -> voice mapping

声线分配通过 roles.json 解析（roles/default_roles 统一管理）。
"""
import json
import os
from pathlib import Path
from typing import Dict

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.tts import run_per_segment as tts_run_per_segment
from dubora.schema.asr_model import AsrModel
from dubora.schema.dub_manifest import dub_manifest_from_asr_model
from dubora.schema.tts_report import tts_report_to_dict
from dubora.utils.logger import info, warning


class TTSPhase(Phase):
    """语音合成 Phase。"""

    name = "tts"
    version = "1.1.0"

    def requires(self) -> list[str]:
        """需要 dub.dub_manifest（SSOT for dubbing）。"""
        return ["dub.dub_manifest"]

    def provides(self) -> list[str]:
        """生成 per-segment WAVs, tts_report, voice_assignment。"""
        return ["tts.segments_dir", "tts.segments_index", "tts.report", "tts.voice_assignment"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 TTS Phase (Timeline-First Architecture)。

        流程：
        1. 读取 tts.manifest.json (SSOT for dubbing)
        2. 通过 roles.json 解析声线分配
        3. TTS per-segment 合成 (VolcEngine)
        4. 生成 tts_report.json
        """
        # 获取输入 (tts.manifest.json)
        dub_manifest_artifact = inputs.get("dub.dub_manifest")
        if not dub_manifest_artifact:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="dub.dub_manifest artifact not found. Make sure align phase completed successfully.",
                ),
            )

        dub_manifest_path = Path(ctx.workspace) / dub_manifest_artifact.relpath
        if not dub_manifest_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Dub manifest file not found: {dub_manifest_path}",
                ),
            )

        workspace_path = Path(ctx.workspace)

        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("tts", {})

        # VolcEngine 配置（支持从环境变量读取）
        volcengine_app_id = (
            phase_config.get("volcengine_app_id") or
            ctx.config.get("volcengine_app_id") or
            os.environ.get("DOUBAO_APPID") or
            os.environ.get("VOLC_APP_ID")
        )
        volcengine_access_key = (
            phase_config.get("volcengine_access_key") or
            ctx.config.get("volcengine_access_key") or
            os.environ.get("DOUBAO_ACCESS_TOKEN") or
            os.environ.get("VOLC_ACCESS_KEY")
        )
        volcengine_resource_id = phase_config.get("volcengine_resource_id", ctx.config.get("volcengine_resource_id", "seed-tts-1.0"))
        volcengine_format = phase_config.get("volcengine_format", ctx.config.get("volcengine_format", "pcm"))
        volcengine_sample_rate = phase_config.get("volcengine_sample_rate", ctx.config.get("volcengine_sample_rate", 24000))

        # 通用配置
        max_workers = phase_config.get("max_workers", ctx.config.get("tts_max_workers", 4))
        language = phase_config.get("language", ctx.config.get("azure_tts_language", "en-US"))

        # 验证 VolcEngine 配置
        if not volcengine_app_id or not volcengine_access_key:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="VolcEngine TTS credentials not set (volcengine_app_id and volcengine_access_key required). "
                            "Set via env: DOUBAO_APPID and DOUBAO_ACCESS_TOKEN, "
                            "or config: phases.tts.volcengine_app_id and phases.tts.volcengine_access_key",
                ),
            )

        try:
            # 读取 dub.json (AsrModel) 并通过适配器转换为 DubManifest
            with open(dub_manifest_path, "r", encoding="utf-8") as f:
                asr_model = AsrModel.from_dict(json.load(f))

            dub_manifest = dub_manifest_from_asr_model(asr_model)
            info(f"Loaded dub manifest: {len(dub_manifest.utterances)} utterances, audio_duration_ms={dub_manifest.audio_duration_ms}")

            if not dub_manifest.utterances:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="ValueError",
                        message="No utterances found in tts.manifest.json.",
                    ),
                )

            # 声线映射文件路径
            dict_dir = workspace_path.parent / "dict"
            roles_path = str(dict_dir / "roles.json")

            # 输出路径
            segments_dir = outputs.get("tts.segments_dir")
            segments_dir.mkdir(parents=True, exist_ok=True)

            # 调用 Processor 层 (per-segment synthesis)
            temp_dir = str(workspace_path / ".cache" / "tts")
            Path(temp_dir).mkdir(parents=True, exist_ok=True)

            episode_id = workspace_path.name
            result = tts_run_per_segment(
                dub_manifest=dub_manifest,
                segments_dir=str(segments_dir),
                roles_path=roles_path,
                volcengine_app_id=volcengine_app_id,
                volcengine_access_key=volcengine_access_key,
                volcengine_resource_id=volcengine_resource_id,
                volcengine_format=volcengine_format,
                volcengine_sample_rate=volcengine_sample_rate,
                language=language,
                max_workers=max_workers,
                temp_dir=temp_dir,
            )

            # 从 ProcessorResult 提取数据
            voice_assignment = result.data["voice_assignment"]
            tts_report = result.data["tts_report"]

            # Check for failures
            if not tts_report.all_succeeded:
                failed_segments = [s for s in tts_report.segments if s.error]
                error_msgs = [f"{s.utt_id}: {s.error}" for s in failed_segments[:5]]
                warning(f"TTS had {tts_report.failed_count} failures: {error_msgs}")

            # Phase 层负责文件 IO：保存 voice_assignment.json
            voice_assignment_path = outputs.get("tts.voice_assignment")
            voice_assignment_path.parent.mkdir(parents=True, exist_ok=True)
            with open(voice_assignment_path, "w", encoding="utf-8") as f:
                json.dump(voice_assignment, f, indent=2, ensure_ascii=False)

            # 保存 tts_report.json
            report_path = outputs.get("tts.report")
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(tts_report_to_dict(tts_report), f, indent=2, ensure_ascii=False)
            info(f"Saved TTS report: {tts_report.total_segments} segments, {tts_report.success_count} succeeded")

            # 生成 segments.json 索引（下游 Mix 消费的干净合约）
            from dubora.pipeline.core.fingerprints import hash_file
            segments_index = {}
            for seg in tts_report.segments:
                if seg.error:
                    continue
                seg_file = segments_dir / seg.output_path
                spk_info = voice_assignment.get("speakers", {}).get(
                    next((u.speaker for u in dub_manifest.utterances if u.utt_id == seg.utt_id), ""),
                    {},
                )
                segments_index[seg.utt_id] = {
                    "wav_path": seg.output_path,
                    "voice_id": spk_info.get("voice_type", ""),
                    "role_id": spk_info.get("role_id", ""),
                    "duration_ms": seg.final_ms,
                    "rate": seg.rate,
                    "hash": hash_file(seg_file) if seg_file.exists() else "",
                }
            segments_index_path = outputs.get("tts.segments_index")
            segments_index_path.parent.mkdir(parents=True, exist_ok=True)
            with open(segments_index_path, "w", encoding="utf-8") as f:
                json.dump(segments_index, f, indent=2, ensure_ascii=False)
            info(f"Saved segments index: {len(segments_index)} entries")

            # 清理临时目录
            temp_path = Path(temp_dir)
            if temp_path.exists():
                for item in temp_path.iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)

            info(f"TTS synthesis completed: {tts_report.success_count}/{tts_report.total_segments} segments")

            # 返回 PhaseResult
            return PhaseResult(
                status="succeeded",
                outputs=["tts.segments_dir", "tts.segments_index", "tts.report", "tts.voice_assignment"],
                metrics={
                    "total_segments": tts_report.total_segments,
                    "success_count": tts_report.success_count,
                    "failed_count": tts_report.failed_count,
                    "audio_duration_ms": dub_manifest.audio_duration_ms,
                },
            )

        except Exception as e:
            import traceback
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                    traceback=traceback.format_exc(),
                ),
            )
