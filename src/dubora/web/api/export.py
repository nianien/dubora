"""
Export API: 从 DB artifacts 表提供最终产物下载（本地优先，GCS 签名 URL 兜底）。
"""
import logging
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from dubora.config.settings import get_workdir
from dubora.pipeline.core.manifest import resolve_artifact_path
from dubora.pipeline.core.store import PipelineStore

router = APIRouter()
logger = logging.getLogger(__name__)

_FILENAME_TO_KIND = {
    "zh.srt": "zh_srt",
    "en.srt": "en_srt",
    "dubbed.mp4": "dubbed_video",
}

# kind → manifest artifact key (for local file resolution)
_KIND_TO_ARTIFACT_KEY = {
    "zh_srt": "subs.zh_srt",
    "en_srt": "subs.en_srt",
    "dubbed_video": "burn.video",
}

_KIND_MEDIA_TYPE = {
    "zh_srt": "text/plain; charset=utf-8",
    "en_srt": "text/plain; charset=utf-8",
    "dubbed_video": "video/mp4",
}


def _get_store(db_path: Path) -> PipelineStore:
    return PipelineStore(db_path)


def _signed_url(gcs_path: str) -> str:
    from dubora.utils.file_store import _gcs_bucket
    blob = _gcs_bucket().blob(gcs_path)
    return blob.generate_signed_url(expiration=timedelta(hours=1))


@router.get("/export/{episode_id}/{filename}")
async def export_file(request: Request, episode_id: int, filename: str):
    """统一下载入口：zh.srt / en.srt / dubbed.mp4。

    优先返回本地文件，本地缺失时 redirect 到 GCS 签名 URL。
    """
    kind = _FILENAME_TO_KIND.get(filename)
    if not kind:
        raise HTTPException(status_code=400, detail=f"Unknown filename: {filename}")

    store = _get_store(request.app.state.db_path)
    ep_row = store.get_episode(episode_id)
    if not ep_row:
        raise HTTPException(status_code=404, detail="Episode not found")

    art = store.get_artifact(episode_id, kind)
    if not art:
        raise HTTPException(status_code=404, detail=f"Artifact '{kind}' not found. Run burn phase first.")

    # 1) 本地文件 (从 manifest 规则算路径)
    artifact_key = _KIND_TO_ARTIFACT_KEY.get(kind)
    if artifact_key:
        workdir = get_workdir(ep_row["drama_name"], ep_row["number"])
        local = resolve_artifact_path(artifact_key, workdir)
        if local.is_file():
            from urllib.parse import quote
            dl_name = f"{ep_row['drama_name']}_EP{ep_row['number']}_{filename}"
            encoded = quote(dl_name)
            return FileResponse(
                local,
                media_type=_KIND_MEDIA_TYPE.get(kind, "application/octet-stream"),
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
                },
            )

    # 2) GCS signed URL redirect
    if art["gcs_path"]:
        try:
            url = _signed_url(art["gcs_path"])
            return RedirectResponse(url)
        except Exception as e:
            logger.error("GCS signed URL failed for %s: %s", art["gcs_path"], e)

    raise HTTPException(
        status_code=404,
        detail="Artifact file not available (local missing, GCS unavailable).",
    )
