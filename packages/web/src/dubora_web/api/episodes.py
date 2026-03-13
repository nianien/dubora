"""
Episodes API: query dramas + episodes from DB
"""
import logging
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel

from dubora_core.config.settings import get_upload_cache_dir, get_workdir
from dubora_core.store import DbStore
from dubora_web.api._helpers import get_user_id, require_drama_owner

router = APIRouter()
logger = logging.getLogger(__name__)


def _file_store(request: Request):
    return request.app.state.file_store


def _get_store(db_path: Path) -> DbStore:
    return DbStore(db_path)


@router.get("/dramas")
async def list_dramas(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    search: str = "",
    status: str = "",
    sort: str = "updated_at",
) -> dict:
    """Return dramas with pagination, search, and filtering."""
    store = _get_store(request.app.state.db_path)
    user_id = get_user_id(request)

    # Base query with episode aggregation
    base = """
        SELECT d.id, d.name, d.synopsis, d.cover_image,
               d.total_episodes,
               COUNT(e.id) AS episode_count,
               MAX(COALESCE(e.updated_at, d.updated_at)) AS updated_at,
               SUM(CASE WHEN e.status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
               SUM(CASE WHEN e.status NOT IN ('ready') AND e.status IS NOT NULL THEN 1 ELSE 0 END) AS started_count
        FROM dramas d
        LEFT JOIN episodes e ON e.drama_id = d.id
    """
    where_clauses: list[str] = []
    params: list = []

    if user_id is not None:
        where_clauses.append("d.user_id = ?")
        params.append(user_id)

    if search:
        where_clauses.append("d.name LIKE ?")
        params.append(f"%{search}%")

    if where_clauses:
        base += " WHERE " + " AND ".join(where_clauses)

    base += " GROUP BY d.id"

    # Status filtering via HAVING on aggregated counts
    if status == "running":
        # 进行中：有集已开始但未全部完成
        base += " HAVING started_count > 0 AND succeeded_count < episode_count"
    elif status == "completed":
        base += " HAVING episode_count > 0 AND succeeded_count = episode_count"
    elif status == "not_started":
        base += " HAVING started_count = 0"

    # Count total before pagination
    count_sql = f"SELECT COUNT(*) AS cnt FROM ({base})"
    total = store._conn.execute(count_sql, params).fetchone()["cnt"]

    # Sorting
    sort_map = {
        "updated_at": "updated_at DESC",
        "created_at": "d.id DESC",
        "name": "d.name ASC",
    }
    order = sort_map.get(sort, "updated_at DESC")
    base += f" ORDER BY {order}"

    # Pagination
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    elif page_size > 100:
        page_size = 100
    offset = (page - 1) * page_size
    base += " LIMIT ? OFFSET ?"
    params.extend([page_size, offset])

    rows = store._conn.execute(base, params).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["cover_image"] = _file_store(request).get_url(d["cover_image"])
        # Remove internal aggregation columns
        d.pop("succeeded_count", None)
        d.pop("started_count", None)
        items.append(d)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


class CreateDramaBody(BaseModel):
    name: str
    total_episodes: int = 0
    synopsis: str = ""


@router.post("/dramas")
async def create_drama(request: Request, body: CreateDramaBody) -> dict:
    """Create a new drama with optional episodes and synopsis."""
    store = _get_store(request.app.state.db_path)
    user_id = get_user_id(request)
    drama_id = store.ensure_drama(name=body.name, synopsis=body.synopsis, user_id=user_id)

    # Set total_episodes
    if body.total_episodes > 0:
        store._conn.execute(
            "UPDATE dramas SET total_episodes=? WHERE id=?",
            (body.total_episodes, drama_id),
        )
        # Batch-create episode records
        for i in range(1, body.total_episodes + 1):
            store.ensure_episode(drama_id=drama_id, number=i)
        store._conn.commit()

    return {"id": drama_id, "name": body.name}


class UpdateDramaBody(BaseModel):
    synopsis: str | None = None
    cover_image: str | None = None


@router.put("/dramas/{drama_id}")
async def update_drama(request: Request, drama_id: int, body: UpdateDramaBody) -> dict:
    """Update drama fields."""
    store = _get_store(request.app.state.db_path)
    require_drama_owner(store, drama_id, get_user_id(request))
    updates: list[str] = []
    params: list = []
    if body.synopsis is not None:
        updates.append("synopsis=?")
        params.append(body.synopsis)
    if body.cover_image is not None:
        updates.append("cover_image=?")
        params.append(body.cover_image)
    if updates:
        from datetime import datetime, timezone
        updates.append("updated_at=?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(drama_id)
        store._conn.execute(
            f"UPDATE dramas SET {', '.join(updates)} WHERE id=?",
            params,
        )
        store._conn.commit()
    row = store._conn.execute("SELECT * FROM dramas WHERE id=?", (drama_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Drama not found")
    return dict(row)


_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@router.post("/dramas/{drama_id}/cover")
async def upload_cover(request: Request, drama_id: int, file: UploadFile) -> dict:
    """Upload a cover image for a drama. Saves as videos/{drama_name}/0{ext}."""
    store = _get_store(request.app.state.db_path)
    require_drama_owner(store, drama_id, get_user_id(request))
    row = store._conn.execute("SELECT id, name FROM dramas WHERE id=?", (drama_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Drama not found")
    drama_name = row["name"]

    # Validate file type
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f"Only {', '.join(_ALLOWED_IMAGE_EXTS)} allowed")

    content = await file.read()

    # Storage path: videos/{drama_name}/0{ext} (cover = episode 0)
    blob_path = f"videos/{drama_name}/0{ext}"

    # Save locally
    local_path = get_upload_cache_dir() / blob_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(content)

    # Upload to GCS
    try:
        from dubora_core.utils.file_store import _gcs_bucket
        blob = _gcs_bucket().blob(blob_path)
        blob.upload_from_string(content, content_type=file.content_type)
    except Exception as e:
        logger.error("GCS upload failed for %s: %s", blob_path, e)
        raise HTTPException(status_code=500, detail="封面上传云存储失败")

    # Update DB
    store._conn.execute(
        "UPDATE dramas SET cover_image=? WHERE id=?",
        (blob_path, drama_id),
    )
    store._conn.commit()

    fs = _file_store(request)
    fs.invalidate(blob_path)
    return {"cover_image": fs.get_url(blob_path)}


_ALLOWED_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm"}


@router.post("/dramas/{drama_id}/videos")
async def upload_video(request: Request, drama_id: int, file: UploadFile) -> dict:
    """Upload a video file for a drama episode.

    Episode number is extracted from filename (e.g. 4.mp4 → ep 4).
    Associates with existing empty episode or creates a new one.
    """
    import re

    store = _get_store(request.app.state.db_path)
    require_drama_owner(store, drama_id, get_user_id(request))
    row = store._conn.execute("SELECT id, name FROM dramas WHERE id=?", (drama_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Drama not found")
    drama_name = row["name"]

    # Validate file type
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_VIDEO_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的视频格式: {ext}")

    # Extract episode number from filename
    # Supported: 14.mp4, 014.mp4, 第14集.mp4
    stem = Path(filename).stem
    m = re.match(r"^0*(\d+)$", stem) or re.match(r"^第0*(\d+)集$", stem)
    if not m:
        raise HTTPException(
            status_code=400,
            detail="文件名格式不正确，请使用集号命名（如 1.mp4、02.mp4、第14集.mp4）",
        )
    ep_number = m.group(1)  # stripped leading zeros

    # Read file content
    content = await file.read()

    # GCS path: videos/{drama_name}/{ep_number}{ext}
    gcs_path = f"videos/{drama_name}/{ep_number}{ext}"
    try:
        from dubora_core.utils.file_store import _gcs_bucket
        blob = _gcs_bucket().blob(gcs_path)
        blob.upload_from_string(content, content_type=file.content_type)
    except Exception as e:
        logger.error("GCS upload failed for %s: %s", gcs_path, e)
        raise HTTPException(status_code=500, detail="视频上传云存储失败")

    # Also save locally for fast access (upload cache)
    local_path = get_upload_cache_dir() / gcs_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(content)

    # Ensure episode record (creates if not exists, updates path if exists)
    video_path = f"videos/{drama_name}/{ep_number}{ext}"
    ep_id = store.ensure_episode(drama_id=drama_id, number=int(ep_number), path=video_path)

    return {"id": ep_id, "episode": int(ep_number), "path": video_path}


@router.get("/episodes")
async def list_episodes(request: Request) -> List[dict]:
    """
    Return all episodes from DB, grouped info included.

    Response:
    [
      {
        "id": 1,
        "drama": "家里家外",
        "drama_id": 10001,
        "episode": "5",
        "path": "videos/家里家外/5.mp4",
        "status": "ready",
        "has_asr_result": true,
        "has_asr_model": false,
        "video_file": "家里家外/5.mp4"
      }
    ]
    """
    store = _get_store(request.app.state.db_path)
    user_id = get_user_id(request)

    if user_id is not None:
        rows = store._conn.execute(
            """SELECT e.id, e.number, e.path, e.status, e.drama_id,
                      e.updated_at,
                      d.name AS drama_name
               FROM episodes e
               JOIN dramas d ON e.drama_id = d.id
               WHERE d.user_id = ?
               ORDER BY d.id, e.number""",
            (user_id,),
        ).fetchall()
    else:
        rows = store._conn.execute(
            """SELECT e.id, e.number, e.path, e.status, e.drama_id,
                      e.updated_at,
                      d.name AS drama_name
               FROM episodes e
               JOIN dramas d ON e.drama_id = d.id
               ORDER BY d.id, e.number""",
        ).fetchall()

    if not rows:
        return []

    # Batch-query which episodes have SRC cues in DB
    if user_id is not None:
        cue_rows = store._conn.execute(
            """SELECT DISTINCT c.episode_id FROM cues c
               JOIN episodes e ON c.episode_id = e.id
               JOIN dramas d ON e.drama_id = d.id
               WHERE d.user_id = ?""",
            (user_id,),
        ).fetchall()
    else:
        cue_rows = store._conn.execute(
            "SELECT DISTINCT episode_id FROM cues",
        ).fetchall()
    episodes_with_cues: set[int] = {r["episode_id"] for r in cue_rows}

    # Batch-query artifacts: {episode_id: set(kind)}
    if user_id is not None:
        art_rows = store._conn.execute(
            """SELECT a.episode_id, a.kind FROM artifacts a
               JOIN episodes e ON a.episode_id = e.id
               JOIN dramas d ON e.drama_id = d.id
               WHERE d.user_id = ?""",
            (user_id,),
        ).fetchall()
    else:
        art_rows = store._conn.execute(
            "SELECT episode_id, kind FROM artifacts",
        ).fetchall()
    art_set: dict[int, set[str]] = {}
    for ar in art_rows:
        art_set.setdefault(ar["episode_id"], set()).add(ar["kind"])

    episodes = []
    for r in rows:
        ep_id = r["id"]
        workdir = get_workdir(r["drama_name"], r["number"])

        input_dir = workdir / "input"
        has_asr_result = input_dir.is_dir() and (input_dir / "asr-result.json").is_file()
        has_asr_model = ep_id in episodes_with_cues

        # video_file should point to original video, not dubbed output
        raw_path = r["path"] or ""
        if raw_path and "/dub/" not in raw_path and "/output/" not in raw_path:
            video_file = raw_path
        else:
            # Legacy data may have dubbed path; derive original from drama/episode
            video_file = f"{r['drama_name']}/{r['number']}.mp4"

        ep_arts = art_set.get(ep_id, set())

        episodes.append({
            "id": ep_id,
            "drama": r["drama_name"],
            "drama_id": r["drama_id"],
            "episode": r["number"],
            "path": r["path"] or "",
            "status": r["status"],
            "updated_at": r["updated_at"],
            "video_file": video_file,
            "has_asr_result": has_asr_result,
            "has_asr_model": has_asr_model,
            "dubbed_video": "dubbed_video" in ep_arts,
            "subtitle_file": "en_srt" in ep_arts,
        })

    return episodes
