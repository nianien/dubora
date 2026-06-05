"""
TTS Phase: 语音合成（Timeline-First Architecture + 增量合成）

支持增量模式：只合成 voice_hash 不匹配的 utterances。
支持多引擎：通过 PipelineConfig.tts_engine 全局切换 volcengine / fish。

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
from dubora_pipeline.schema.dub_manifest import dub_manifest_from_utterances
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
    """语音合成 Phase（支持增量合成 + 多引擎）。"""

    name = "tts"
    version = "2.1.0"

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

        # 引擎选择: phase config > global config > default
        tts_engine = (
            phase_config.get("tts_engine") or
            ctx.config.get("tts_engine", "volcengine")
        )
        info(f"TTS engine: {tts_engine}")

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

        # Fish Audio 配置
        fish_api_key = (
            phase_config.get("fish_api_key") or
            ctx.config.get("fish_api_key") or
            os.environ.get("FISH_API_KEY")
        )

        # 凭证校验
        if tts_engine == "volcengine" and (not volcengine_app_id or not volcengine_access_key):
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="VolcEngine TTS credentials not set. "
                            "Set env: DOUBAO_APPID and DOUBAO_ACCESS_TOKEN",
                ),
            )
        if tts_engine == "fish" and not fish_api_key:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="Fish Audio API key not set. Set env: FISH_API_KEY",
                ),
            )

        try:
            # Episode + drama 元数据
            ep = store.get_episode(episode_id)
            drama_id = ep["drama_id"] if ep else 0
            roles_map = store.get_roles_by_id(drama_id)        # {role_id_int: voice_type}
            role_names = store.get_role_name_map(drama_id)     # {role_id_int: name}

            # Read all utterances from DB. role_id / voice_type 必须显式分配，
            # 不存在"默认 role"或"硬编码兜底音色"，未分配直接 fail-fast。
            all_utts = store.get_utterances(episode_id)

            full_manifest = dub_manifest_from_utterances(all_utts, audio_duration_ms)
            info(f"Loaded {len(all_utts)} utterances from DB, {len(full_manifest.utterances)} with translations, audio_duration_ms={full_manifest.audio_duration_ms}")

            # Fail-fast: 任何 utterance 未分配 role 或对应 role 缺 voice_type 都报错
            no_role_count, missing_voice_roles = self._check_voice_assignment(
                all_utts, roles_map, role_names,
            )
            if no_role_count or missing_voice_roles:
                parts: list[str] = []
                if no_role_count:
                    parts.append(
                        f"存在 {no_role_count} 条字幕未指定角色，请在字幕编辑页为相应字幕指定角色"
                    )
                if missing_voice_roles:
                    names = "、".join(f"【{n}】" for n in sorted(missing_voice_roles))
                    parts.append(
                        f"角色{names}未设置音色，请到「角色管理」页面完成设置"
                    )
                msg = "；".join(parts) + "。"
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(type="VoiceAssignmentError", message=msg),
                )

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

            # Fish 克隆参考音频：per-episode + per-role 截取（sample 不跨集复用）
            sample_audio_map = None
            if tts_engine == "fish":
                sample_audio_map = self._ensure_role_samples(
                    store=store,
                    episode_id=episode_id,
                    drama_id=drama_id,
                    drama_name=ep["drama_name"],
                    ep_number=ep["number"],
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
                tts_engine=tts_engine,
                volcengine_app_id=volcengine_app_id,
                volcengine_access_key=volcengine_access_key,
                volcengine_resource_id=volcengine_resource_id,
                volcengine_format=volcengine_format,
                volcengine_sample_rate=volcengine_sample_rate,
                fish_api_key=fish_api_key,
                language=language,
                max_workers=max_workers,
                temp_dir=temp_dir,
                sample_audio_map=sample_audio_map,
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
            info("Debug: saved TTS report + voice assignment")

            # Generate segments.json index
            from dubora_pipeline.fingerprints import hash_file
            segments_index = {}
            for seg in tts_report.segments:
                if seg.error:
                    continue
                seg_file = segments_dir / seg.output_path
                role_key = next(
                    (str(u.role_id) for u in dub_manifest.utterances
                     if u.utt_id == seg.utt_id and u.role_id is not None),
                    "",
                )
                spk_info = voice_assignment.get("speakers", {}).get(role_key, {})
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
                            db_utt.get("role_id"),
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
                        rid = utt.get("role_id")
                        role_name = role_names.get(rid, str(rid)) if rid is not None else ""
                        drift_warnings.append(
                            f"utt {utt['id']} (speaker={utt.get('speaker','')}, role={role_name}): "
                            f"drift={drift:.2f}"
                        )
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
        self,
        utts: list[dict],
        roles_map: dict[int, str],
        role_names: dict[int, str],
    ) -> tuple[int, set[str]]:
        """检查每条 utt 是否显式分配了 role 且对应 role 有 voice_type。

        Returns:
            (no_role_count, missing_voice_role_names)
            - no_role_count: utt.role_id 为 None 的条数
            - missing_voice_role_names: role 存在但 voice_type 为空的角色展示名集合
        """
        no_role_count = 0
        missing_voice_roles: set[str] = set()
        for u in utts:
            rid = u.get("role_id")
            if rid is None:
                no_role_count += 1
                continue
            if not roles_map.get(rid):
                missing_voice_roles.add(role_names.get(rid, str(rid)))
        return no_role_count, missing_voice_roles

    def _ensure_role_samples(
        self,
        *,
        store: DbStore,
        episode_id: int,
        drama_id: int,
        drama_name: str,
        ep_number: int,
        workspace_path: Path,
    ) -> dict[str, str]:
        """为 drama 内每个 role 准备 sample 参考音频（drama 级，跨集复用）。

        设计原则（drama 级 + 跨集采样）:
          - role 是 drama 级实体，sample 也是 drama 级，一旦截过就跨集复用
          - sample 候选池：该 role 在 drama 内所有 episode 的 cue.role_id == role.id
          - 已有 sample（本地或 GCS）→ 直接复用，不重新采
          - 否则从候选 cue 中选最优一条（接近 5 秒），从对应 episode 的 vocals 截取
          - 若候选 cue 所在 episode 的 vocals 本地不可用，则取次优

        筛选条件：speech、2-8 秒、>=4 字。

        路径约定（drama 级）：
          local: data/pipeline/{drama}/roles/{role_id}_sample.wav
          gcs:   dramas/{drama}/roles/{role_id}_sample.wav

        Returns:
            {role_id_str: local_path_str} 已就绪的 sample map
        """
        from dubora_core.config.settings import get_drama_dir, get_workdir
        from dubora_core.utils.file_store import get_gcs_store

        # Drama 级 samples 目录
        samples_dir = get_drama_dir(drama_name) / "roles"
        samples_dir.mkdir(parents=True, exist_ok=True)

        roles = store.get_roles(drama_id)
        role_names = store.get_role_name_map(drama_id)
        gcs = get_gcs_store()

        result: dict[str, str] = {}

        # Step 1: 收集已有 sample 的 role（本地优先，否则 GCS 拉）
        roles_needing: list[dict] = []
        for role in roles:
            role_id = role["id"]
            local_path = samples_dir / f"{role_id}_sample.wav"
            if local_path.exists():
                result[str(role_id)] = str(local_path)
                continue
            # 本地不在 → 看 DB 记录的 GCS key，能拉就拉
            gcs_key = role.get("sample_audio", "")
            if gcs_key:
                try:
                    downloaded = gcs.get(gcs_key)
                    if Path(downloaded).exists():
                        result[str(role_id)] = str(downloaded)
                        info(f"Reused sample for role {role_names.get(role_id, role_id)} from GCS: {gcs_key}")
                        continue
                except Exception as e:
                    warning(f"GCS download failed for role {role_id} ({gcs_key}): {e}")
            roles_needing.append(role)

        if not roles_needing:
            info("All roles already have samples, skipping extraction")
            return result

        # Step 2: 给缺 sample 的 role 跨集找 cue 候选
        def _quality_filter(c: dict) -> bool:
            return (
                c.get("kind") == "speech"
                and (c["end_ms"] - c["start_ms"]) >= 2000
                and (c["end_ms"] - c["start_ms"]) <= 8000
                and len(c.get("text") or "") >= 4
            )

        PAD_MS = 100
        # Vocals 路径缓存（每集只 probe 一次 duration）
        vocals_cache: dict[int, tuple[Path, int]] = {}  # ep_number -> (vocals_path, duration_ms)

        def _resolve_vocals(ep_num: int) -> tuple[Path | None, int]:
            if ep_num in vocals_cache:
                return vocals_cache[ep_num]
            if ep_num == ep_number:
                vp = resolve_artifact_path("extract.vocals", workspace_path)
            else:
                other_workspace = get_workdir(drama_name, ep_num)
                vp = resolve_artifact_path("extract.vocals", other_workspace)
            if not vp.exists():
                vocals_cache[ep_num] = (None, 0)
                return None, 0
            try:
                dur = _probe_duration_ms(str(vp))
            except RuntimeError as e:
                warning(f"Could not probe vocals duration for ep {ep_num}: {e}")
                vocals_cache[ep_num] = (None, 0)
                return None, 0
            vocals_cache[ep_num] = (vp, dur)
            return vp, dur

        for role in roles_needing:
            role_id = role["id"]
            role_name = role_names.get(role_id, str(role_id))

            # 跨集候选 cue（含 episode_id / episode_number）
            cue_pool = store.get_cues_for_role(drama_id, role_id)
            cue_pool = [c for c in cue_pool if _quality_filter(c)]
            if not cue_pool:
                warning(f"No suitable cue across drama for role {role_name} (id={role_id})")
                continue

            # 按"接近 5 秒"排序，逐条尝试（vocals 不可用则取次优）
            cue_pool.sort(key=lambda c: abs((c["end_ms"] - c["start_ms"]) - 5000))

            extracted = False
            for best_cue in cue_pool:
                ep_num = best_cue["episode_number"]
                vocals_path, vocals_dur = _resolve_vocals(ep_num)
                if vocals_path is None:
                    continue  # 该集 vocals 不可用，试下一条候选

                start_s = max(0, (best_cue["start_ms"] - PAD_MS)) / 1000.0
                end_s = min(vocals_dur, (best_cue["end_ms"] + PAD_MS)) / 1000.0
                duration_s = end_s - start_s

                output_path = samples_dir / f"{role_id}_sample.wav"
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
                    warning(f"FFmpeg failed for role {role_id} from ep {ep_num}: {e.stderr}")
                    continue
                if not output_path.exists():
                    continue

                info(
                    f"Extracted sample for role {role_name} (id={role_id}) "
                    f"from ep {ep_num}: {best_cue['start_ms']}ms-{best_cue['end_ms']}ms"
                )

                # 上传 GCS（drama 级 key） + 写回 DB
                gcs_key = f"dramas/{drama_name}/roles/{role_id}_sample.wav"
                try:
                    gcs.write_file(output_path, gcs_key)
                    store.update_role_sample_audio(role_id, gcs_key)
                    info(f"Uploaded sample to GCS + DB: {gcs_key}")
                except Exception as e:
                    warning(f"GCS upload / DB writeback failed for role {role_id}: {e}")

                result[str(role_id)] = str(output_path)
                extracted = True
                break

            if not extracted:
                warning(
                    f"Could not extract sample for role {role_name} (id={role_id}): "
                    f"no candidate cue's episode has local vocals available"
                )

        return result
