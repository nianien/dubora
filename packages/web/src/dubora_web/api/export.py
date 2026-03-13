"""
Export API: 从 DB artifacts 表提供最终产物下载（本地优先，GCS 代理下载兜底）。
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from dubora_core.config.settings import get_workdir, get_gcs_cache_dir
from dubora_core.manifest import resolve_artifact_path
from dubora_core.store import DbStore
from dubora_web.api._helpers import get_user_id, require_episode_owner

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


def _get_store(db_path: Path) -> DbStore:
    return DbStore(db_path)


def _download_from_gcs(gcs_path: str) -> Path | None:
    """Download from GCS to local cache, return local path."""
    try:
        from dubora_core.utils.file_store import _gcs_bucket
        local = get_gcs_cache_dir() / gcs_path
        if local.is_file():
            return local
        blob = _gcs_bucket().blob(gcs_path)
        if not blob.exists():
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local))
        logger.info("Downloaded from GCS: %s", gcs_path)
        return local
    except Exception as e:
        logger.error("GCS download failed for %s: %s", gcs_path, e)
        return None


@router.get("/export/{episode_id}/{filename}")
async def export_file(request: Request, episode_id: int, filename: str):
    """统一下载入口：zh.srt / en.srt / dubbed.mp4。

    优先返回本地文件，本地缺失时 redirect 到 GCS 签名 URL。
    """
    kind = _FILENAME_TO_KIND.get(filename)
    if not kind:
        raise HTTPException(status_code=400, detail=f"Unknown filename: {filename}")

    store = _get_store(request.app.state.db_path)
    require_episode_owner(store, episode_id, get_user_id(request))
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

    # 2) GCS download fallback (proxy, not redirect — video elements can't follow cross-origin redirects)
    if art["gcs_path"]:
        gcs_local = _download_from_gcs(art["gcs_path"])
        if gcs_local and gcs_local.is_file():
            return FileResponse(
                gcs_local,
                media_type=_KIND_MEDIA_TYPE.get(kind, "application/octet-stream"),
                headers={"Accept-Ranges": "bytes"},
            )

    raise HTTPException(
        status_code=404,
        detail="Artifact file not available (local missing, GCS unavailable).",
    )
