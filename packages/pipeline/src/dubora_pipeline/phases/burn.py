"""
Burn Phase: 生成 SRT 字幕 + 烧录字幕到视频 + 上传 GCS + 写 artifacts 表
"""
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_core.utils.file_store import get_gcs_store
from dubora_core.utils.logger import info
from dubora_pipeline.utils.timecode import write_srt_from_segments

logger = logging.getLogger(__name__)


class BurnPhase(Phase):
    """生成 SRT + 烧录字幕 Phase。"""

    name = "burn"
    version = "2.0.0"

    def requires(self) -> list[str]:
        """需要 mix.audio。SRT 从 DB cues 生成。"""
        return ["mix.audio"]

    def provides(self) -> list[str]:
        """生成 burn.video。"""
        return ["burn.video"]

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Burn Phase。

        流程：
        1. 从 DB cues 生成 en.srt + zh.srt
        2. 使用 ffmpeg 将字幕烧录到视频
        3. 上传 GCS
        4. 写 artifacts 表
        """
        store = ctx.store
        episode_id = ctx.episode_id
        workspace_path = Path(ctx.workspace)

        if not store or not episode_id:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="Burn requires DB store and episode_id.",
                ),
            )

        # 获取混音音频
        mix_audio_artifact = inputs["mix.audio"]
        mix_path = workspace_path / mix_audio_artifact.relpath

        if not mix_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Mix audio not found: {mix_path}",
                ),
            )

        # 获取 episode 信息（用于文件命名）
        ep_row = store.get_episode(episode_id)
        ep_number = ep_row["number"] if ep_row else 0

        # 从 DB cues 生成 en.srt + zh.srt
        all_cues = store.get_cues(episode_id)

        en_segments = []
        zh_segments = []
        for cue in all_cues:
            start = cue["start_ms"] / 1000.0
            end = cue["end_ms"] / 1000.0
            text_en = (cue.get("text_en") or "").strip()
            text_cn = (cue.get("text") or "").strip()
            if text_en:
                en_segments.append({"start": start, "end": end, "en_text": text_en})
            if text_cn:
                zh_segments.append({"start": start, "end": end, "zh_text": text_cn})

        en_segments.sort(key=lambda x: x["start"])
        zh_segments.sort(key=lambda x: x["start"])

        if not en_segments:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No translated cues found. Run translate phase first.",
                ),
            )

        output_dir = workspace_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        en_srt_path = output_dir / f"{ep_number}-en.srt"
        write_srt_from_segments(en_segments, str(en_srt_path), text_key="en_text")
        info(f"Generated {en_srt_path.name}: {len(en_segments)} segments")

        zh_srt_path = output_dir / f"{ep_number}-zh.srt"
        write_srt_from_segments(zh_segments, str(zh_srt_path), text_key="zh_text")
        info(f"Generated {zh_srt_path.name}: {len(zh_segments)} segments")

        # 获取 video_path（从 config）
        video_path = ctx.config.get("video_path")
        if not video_path:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="video_path not found in config",
                ),
            )

        # 输出视频路径（使用 runner 预分配的 outputs）
        output_video_path = outputs.get("burn.video")

        try:
            # 转义路径（Windows 兼容）
            escaped_srt = os.path.abspath(en_srt_path).replace("\\", "\\\\").replace(":", "\\:")

            cmd = [
                "ffmpeg",
                "-i", str(video_path),
                "-i", str(mix_path),
                "-vf", f"subtitles={escaped_srt}",
                "-c:v", "libx264",
                "-c:a", "aac",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-metadata:s:v", "rotate=0",
                "-movflags", "+faststart",
                "-y",
                str(output_video_path),
            ]

            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )

            if not output_video_path.exists():
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message=f"Burn failed: {output_video_path} was not created",
                    ),
                )

            info(f"Burn completed: {output_video_path.name} (size: {output_video_path.stat().st_size / 1024 / 1024:.2f} MB)")

            # ── Publish deliverables to drama-level dub/ and sub/ ──
            import shutil
            drama_name = ep_row["drama_name"] if ep_row else "unknown"
            drama_dir = workspace_path.parent  # {ep} → {drama}
            dub_dir = drama_dir / "dub"
            sub_dir = drama_dir / "sub"
            dub_dir.mkdir(parents=True, exist_ok=True)
            sub_dir.mkdir(parents=True, exist_ok=True)

            copies = [
                (output_video_path, dub_dir / f"{ep_number}-dubbed.mp4"),
                (zh_srt_path, sub_dir / f"{ep_number}-zh.srt"),
                (en_srt_path, sub_dir / f"{ep_number}-en.srt"),
            ]
            for src, dst in copies:
                shutil.copy2(src, dst)
            info("Published deliverables to dub/ and sub/")

            # ── GCS upload + write artifacts 表 ──
            gcs = get_gcs_store()
            deliverables = [
                ("zh_srt",       zh_srt_path,        f"dramas/{drama_name}/sub/{ep_number}-zh.srt"),
                ("en_srt",       en_srt_path,        f"dramas/{drama_name}/sub/{ep_number}-en.srt"),
                ("dubbed_video", output_video_path,   f"dramas/{drama_name}/dub/{ep_number}-dubbed.mp4"),
            ]

            for kind, src_path, gcs_key in deliverables:
                gcs.write_file(src_path, gcs_key)
                checksum = gcs.get_local_sha(gcs_key)
                info(f"Uploaded {kind} to GCS: {gcs_key}")
                store.upsert_artifact(
                    episode_id,
                    kind,
                    gcs_path=gcs_key,
                    checksum=checksum,
                )

            return PhaseResult(
                status="succeeded",
                outputs=["burn.video"],
                metrics={
                    "output_video_size_mb": output_video_path.stat().st_size / 1024 / 1024,
                },
            )

        except subprocess.CalledProcessError as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message=f"FFmpeg failed: {e.stderr or e.stdout or 'Unknown error'}",
                ),
            )
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )
