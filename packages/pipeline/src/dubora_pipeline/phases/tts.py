"""
TTS Phase: 语音合成（Timeline-First Architecture + 增量合成）

支持增量模式：只合成 voice_hash 不匹配的 utterances。

输入: extract.audio (for duration probing), enriched utterances from DB
输出:
  - tts.segments_dir: Per-segment WAV files
  - tts.report: TTS synthesis report (JSON)
  - tts.voice_assignment: Speaker -> voice mapping

声线分配通过 DB roles 表解析。
"""
import json
import os
import subprocess
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_core.store import DbStore, _compute_voice_hash
from dubora_core.manifest import resolve_artifact_path
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_pipeline.processors.tts import run_per_segment as tts_run_per_segment
from dubora_pipeline.schema.dub_manifest import dub_manifest_from_utterances, DubManifest, DubUtterance, TTSPolicy
from dubora_pipeline.schema.tts_report import tts_report_to_dict
from dubora_core.utils.logger import info, warning


def _probe_duration_ms(audio_path: str) -> int:
    """Probe audio duration using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        raise RuntimeError(f"ffprobe returned invalid duration for {audio_path}")
    return int(float(duration_str) * 1000)


class TTSPhase(Phase):
    """语音合成 Phase（支持增量合成）。"""

    name = "tts"
    version = "2.0.0"

    def requires(self) -> list[str]:
        """需要 extract.audio（用于 probe duration）。数据从 DB 读取。"""
        return ["extract.audio"]

    def provides(self) -> list[str]:
        """生成 per-segment WAVs。"""
        return ["tts.segments_dir"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """执行 TTS Phase。增量模式下只合成 voice_hash 不匹配的 utterances。"""
        workspace_path = Path(ctx.workspace)
        store = ctx.store
        episode_id = ctx.episode_id

        if not store or not episode_id:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="TTS requires DB store and episode_id. Ensure pipeline is running in DB mode.",
                ),
            )

        # Probe audio duration from extract.audio
        audio_artifact = inputs.get("extract.audio")
        audio_duration_ms = 0
        if audio_artifact:
            audio_path = workspace_path / audio_artifact.relpath
            if audio_path.exists():
                try:
                    audio_duration_ms = _probe_duration_ms(str(audio_path))
                    info(f"Probed audio duration: {audio_duration_ms}ms")
                except RuntimeError as e:
                    warning(f"Could not probe audio duration: {e}")

        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("tts", {})

        # VolcEngine 配置
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
        max_workers = phase_config.get("max_workers", ctx.config.get("tts_max_workers", 4))
        language = phase_config.get("language", ctx.config.get("azure_tts_language", "en-US"))

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
            # Read all utterances from DB
            all_utts = store.get_utterances(episode_id)
            full_manifest = dub_manifest_from_utterances(all_utts, audio_duration_ms)
            info(f"Loaded {len(all_utts)} utterances from DB, {len(full_manifest.utterances)} with translations, audio_duration_ms={full_manifest.audio_duration_ms}")

            # Find dirty utterances (voice_hash mismatch)
            dirty_utts = store.get_dirty_utterances_for_tts(episode_id)
            info(f"Incremental TTS: {len(dirty_utts)} dirty utterances (voice_hash mismatch)")

            if not dirty_utts:
                info("No dirty utterances, TTS is a no-op")
                self._write_noop_outputs(outputs, workspace_path)
                return PhaseResult(
                    status="succeeded",
                    outputs=["tts.segments_dir"],
                    metrics={"total_segments": 0, "success_count": 0, "failed_count": 0, "incremental": True},
                )

            # Build DubManifest from dirty utterances only
            dub_manifest = dub_manifest_from_utterances(dirty_utts, full_manifest.audio_duration_ms)
            info(f"Built manifest from {len(dub_manifest.utterances)} dirty utterances for TTS")

            if not dub_manifest.utterances:
                info("No synthesizable utterances after filtering, TTS is a no-op")
                self._write_noop_outputs(outputs, workspace_path)
                return PhaseResult(
                    status="succeeded",
                    outputs=["tts.segments_dir"],
                    metrics={"total_segments": 0, "success_count": 0, "failed_count": 0, "incremental": True},
                )

            # Voice assignment check (DB-backed, id-keyed)
            ep = store.get_episode(episode_id)
            drama_id = ep["drama_id"] if ep else 0
            roles_map = store.get_roles_by_id(drama_id)       # {role_id_int: voice_type}
            role_names = store.get_role_name_map(drama_id)     # {role_id_int: name}

            unassigned = self._check_voice_assignment(all_utts, roles_map, role_names)
            if unassigned:
                names = ", ".join(sorted(unassigned))
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="VoiceAssignmentError",
                        message=f"\u4ee5\u4e0b\u89d2\u8272\u672a\u5206\u914d\u97f3\u8272\uff0c\u8bf7\u5728 Voice Casting \u4e2d\u5b8c\u6210\u5206\u914d\u540e\u91cd\u8bd5: {names}",
                    ),
                )

            # Ensure role sample audio exists for each role
            self._ensure_role_samples(
                store=store,
                episode_id=episode_id,
                drama_id=drama_id,
                drama_name=ep["drama_name"],
                workspace_path=workspace_path,
            )

            # TTS synthesis
            segments_dir = outputs.get("tts.segments_dir")
            segments_dir.mkdir(parents=True, exist_ok=True)

            temp_dir = str(workspace_path / ".cache" / "tts")
            Path(temp_dir).mkdir(parents=True, exist_ok=True)

            # Convert int-keyed roles_map to str-keyed for processor compatibility
            str_roles_map = {str(k): v for k, v in roles_map.items()}
            result = tts_run_per_segment(
                dub_manifest=dub_manifest,
                segments_dir=str(segments_dir),
                roles_map=str_roles_map,
                volcengine_app_id=volcengine_app_id,
                volcengine_access_key=volcengine_access_key,
                volcengine_resource_id=volcengine_resource_id,
                volcengine_format=volcengine_format,
                volcengine_sample_rate=volcengine_sample_rate,
                language=language,
                max_workers=max_workers,
                temp_dir=temp_dir,
            )

            voice_assignment = result.data["voice_assignment"]
            tts_report = result.data["tts_report"]

            if not tts_report.all_succeeded:
                failed_segments = [s for s in tts_report.segments if s.error]
                error_msgs = [f"{s.utt_id}: {s.error}" for s in failed_segments[:5]]
                warning(f"TTS had {tts_report.failed_count} failures: {error_msgs}")

            # Save debug files
            va_path = workspace_path / "voice-assignment.json"
            with open(va_path, "w", encoding="utf-8") as f:
                json.dump(voice_assignment, f, indent=2, ensure_ascii=False)

            report_path = workspace_path / "tts" / "report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(tts_report_to_dict(tts_report), f, indent=2, ensure_ascii=False)
            info(f"Debug: saved TTS report + voice assignment")

            # Generate segments.json index
            from dubora_pipeline.fingerprints import hash_file
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
            segments_index_path = workspace_path / "tts" / "segments.json"
            with open(segments_index_path, "w", encoding="utf-8") as f:
                json.dump(segments_index, f, indent=2, ensure_ascii=False)
            info(f"Debug: saved segments index ({len(segments_index)} entries)")

            # Update DB: write TTS results + voice_hash
            utt_by_utt_id = {}
            for db_utt in dirty_utts:
                uid = f"utt_{db_utt['id']:08x}" if isinstance(db_utt.get("id"), int) else str(db_utt["id"])
                utt_by_utt_id[uid] = db_utt

            for seg in tts_report.segments:
                db_utt = utt_by_utt_id.get(seg.utt_id)
                if not db_utt:
                    continue
                if seg.error:
                    store.update_utterance(
                        db_utt["id"],
                        tts_error=seg.error,
                        tts_duration_ms=0,
                        tts_rate=0.0,
                    )
                else:
                    store.update_utterance(
                        db_utt["id"],
                        audio_path=str(Path(seg.output_path)),
                        tts_duration_ms=seg.final_ms,
                        tts_rate=seg.rate,
                        tts_error=None,
                        voice_hash=_compute_voice_hash(
                            db_utt.get("text_en", ""),
                            db_utt.get("speaker", ""),
                            db_utt.get("emotion", ""),
                        ),
                    )
            info(f"Updated TTS results for {len(tts_report.segments)} utterances in DB")

            # Clean temp dir
            temp_path = Path(temp_dir)
            if temp_path.exists():
                for item in temp_path.iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)

            info(f"TTS synthesis completed: {tts_report.success_count}/{tts_report.total_segments} segments")

            # Drift score check
            drift_warnings = []
            for utt in all_utts:
                physical_ms = utt["end_ms"] - utt["start_ms"]
                tts_ms = utt.get("tts_duration_ms") or 0
                if physical_ms > 0 and tts_ms > 0:
                    drift = tts_ms / physical_ms
                    if drift > 1.1:
                        spk = role_names.get(int(utt.get("speaker", 0)), str(utt.get("speaker", "")))
                        drift_warnings.append(f"utt {utt['id']} ({spk}): drift={drift:.2f}")
            if drift_warnings:
                for w in drift_warnings:
                    warning(f"Drift: {w}")

            return PhaseResult(
                status="succeeded",
                outputs=["tts.segments_dir"],
                metrics={
                    "total_segments": tts_report.total_segments,
                    "success_count": tts_report.success_count,
                    "failed_count": tts_report.failed_count,
                    "audio_duration_ms": full_manifest.audio_duration_ms,
                    "drift_warnings": len(drift_warnings),
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

    def _write_noop_outputs(self, outputs, workspace_path):
        """Write minimal output files for no-op case."""
        segments_dir = outputs.get("tts.segments_dir")
        segments_dir.mkdir(parents=True, exist_ok=True)

    def _check_voice_assignment(
        self, utts: list[dict], roles_map: dict[int, str], role_names: dict[int, str],
    ) -> set[str]:
        """检查所有 speaker(role_id) 是否已在 DB roles 中分配音色。返回未分配角色的名字。"""
        speakers = set()
        for u in utts:
            spk = u.get("speaker")
            if spk:
                try:
                    speakers.add(int(spk))
                except (ValueError, TypeError):
                    pass
        unassigned = set()
        for spk_id in speakers:
            if not roles_map.get(spk_id):
                unassigned.add(role_names.get(spk_id, str(spk_id)))
        return unassigned

    def _ensure_role_samples(
        self,
        *,
        store: DbStore,
        episode_id: int,
        drama_id: int,
        drama_name: str,
        workspace_path: Path,
    ) -> None:
        """为缺少样本音频的角色从 vocals 中提取参考音频片段。

        选择质量最优的 cue（speech、2-8秒、>=4字），从 vocals.wav 截取并上传 GCS。
        """
        roles = store.get_roles(drama_id)
        roles_needing_sample = [r for r in roles if not r.get("sample_audio")]
        if not roles_needing_sample:
            info("All roles already have sample audio, skipping extraction")
            return

        # Resolve vocals path
        vocals_path = resolve_artifact_path("extract.vocals", workspace_path)
        if not vocals_path.exists():
            warning(f"Vocals file not found at {vocals_path}, skipping role sample extraction")
            return

        # Probe vocals duration
        try:
            audio_duration_ms = _probe_duration_ms(str(vocals_path))
        except RuntimeError as e:
            warning(f"Could not probe vocals duration: {e}, skipping role sample extraction")
            return

        cues = store.get_cues(episode_id)

        # Output directory for role samples (drama-level)
        roles_dir = workspace_path.parent / "roles"
        roles_dir.mkdir(parents=True, exist_ok=True)

        PAD_MS = 100

        for role in roles_needing_sample:
            role_id = role["id"]
            role_name = role["name"]

            # Filter cues for this role
            role_cues = [
                c for c in cues
                if c.get("speaker") == role_id
                and c.get("kind") == "speech"
                and (c["end_ms"] - c["start_ms"]) >= 2000
                and (c["end_ms"] - c["start_ms"]) <= 8000
                and len(c.get("text") or "") >= 4
            ]

            if not role_cues:
                warning(f"No suitable cue found for role {role_name} (id={role_id}), skipping sample extraction")
                continue

            # Sort: prefer duration closest to 4-6 seconds (5000ms center)
            role_cues.sort(key=lambda c: abs((c["end_ms"] - c["start_ms"]) - 5000))
            best_cue = role_cues[0]

            # Extract audio clip with padding
            start_s = max(0, (best_cue["start_ms"] - PAD_MS)) / 1000.0
            end_s = min(audio_duration_ms, (best_cue["end_ms"] + PAD_MS)) / 1000.0
            duration_s = end_s - start_s

            output_path = roles_dir / f"{role_id}_sample.wav"
            cmd = [
                "ffmpeg", "-y",
                "-i", str(vocals_path),
                "-ss", f"{start_s:.3f}",
                "-t", f"{duration_s:.3f}",
                "-ar", "24000", "-ac", "1",
                str(output_path),
            ]

            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                warning(f"FFmpeg failed extracting sample for role {role_name}: {e.stderr}")
                continue

            if not output_path.exists():
                warning(f"Sample file not created for role {role_name}")
                continue

            from dubora_core.utils.file_store import get_gcs_store

            info(f"Extracted sample for role {role_name} (id={role_id}): "
                 f"{best_cue['start_ms']}ms-{best_cue['end_ms']}ms -> {output_path.name}")

            # Upload to GCS (best-effort, failure does not abort TTS)
            gcs = get_gcs_store()
            gcs_key = f"dramas/{drama_name}/roles/{role_id}_sample.wav"
            try:
                gcs.write_file(output_path, gcs_key)
                info(f"Uploaded role sample to GCS: {gcs_key}")
                store.update_role_sample_audio(role_id, gcs_key)
            except Exception as e:
                warning(f"GCS upload failed for role sample {role_name}: {e}")
