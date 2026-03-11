"""
Burn Phase: 生成 SRT 字幕 + 烧录字幕到视频 + 上传 GCS + 写 artifacts 表
"""
import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict

from dubora_pipeline.phase import Phase
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_core.utils.logger import info
from dubora_pipeline.utils.timecode import write_srt_from_segments

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _upload_gcs(local_path: Path, gcs_path: str) -> bool:
    """Best-effort upload to GCS. Returns True on success."""
    try:
        from dubora_core.utils.file_store import _gcs_bucket
        blob = _gcs_bucket().blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        return True
    except Exception as e:
        logger.warning("GCS upload failed for %s: %s", gcs_path, e)
        return False


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
        3. 上传 GCS (best-effort)
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

        output_dir = workspace_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        en_srt_path = output_dir / "en.srt"
        write_srt_from_segments(en_segments, str(en_srt_path), text_key="en_text")
        info(f"Generated en.srt: {len(en_segments)} segments")

        zh_srt_path = output_dir / "zh.srt"
        write_srt_from_segments(zh_segments, str(zh_srt_path), text_key="zh_text")
        info(f"Generated zh.srt: {len(zh_segments)} segments")

        if not en_segments:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No translated cues found. Run translate phase first.",
                ),
            )

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

            # ── 写 artifacts 表 + GCS 上传 ──
            ep_row = store.get_episode(episode_id)
            drama_name = ep_row["drama_name"] if ep_row else "unknown"
            ep_number = ep_row["number"] if ep_row else 0
            gcs_prefix = f"videos/{drama_name}/{ep_number}"

            deliverables = [
                ("zh_srt", zh_srt_path, f"{gcs_prefix}-zh.srt"),
                ("en_srt", en_srt_path, f"{gcs_prefix}-en.srt"),
                ("dubbed_video", output_video_path, f"{gcs_prefix}-dubbed.mp4"),
            ]

            for kind, local_abs, gcs_key in deliverables:
                checksum = _sha256_file(local_abs) if local_abs.is_file() else None
                uploaded = _upload_gcs(local_abs, gcs_key)
                if uploaded:
                    info(f"Uploaded {kind} to GCS: {gcs_key}")
                else:
                    info(f"GCS upload skipped/failed for {kind}: {gcs_key}")
                store.upsert_artifact(
                    episode_id,
                    kind,
                    gcs_path=gcs_key if uploaded else None,
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
