"""
Media API: 静态文件服务，支持 Range header（视频 seek）
对非 faststart 的 MP4 自动 remux（moov atom 前置）
"""
import logging
import mimetypes
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from dubora_core.config.settings import get_data_dir, get_faststart_cache_dir, get_gcs_cache_dir

router = APIRouter()
logger = logging.getLogger(__name__)


def _needs_faststart(file_path: Path) -> bool:
    """检测 MP4 文件是否需要 faststart（moov atom 在 mdat 之后）。"""
    try:
        with open(file_path, "rb") as f:
            # 扫描前 100MB 内的顶层 atom
            scanned = 0
            limit = 100 * 1024 * 1024
            while scanned < limit:
                header = f.read(8)
                if len(header) < 8:
                    break
                size = int.from_bytes(header[:4], "big")
                atom_type = header[4:8]
                if atom_type == b"moov":
                    return False  # moov 在前，不需要 faststart
                if atom_type == b"mdat":
                    return True   # mdat 在前，需要 faststart
                if size == 0:
                    break  # atom extends to EOF
                if size == 1:
                    # 64-bit extended size
                    ext = f.read(8)
                    if len(ext) < 8:
                        break
                    size = int.from_bytes(ext, "big")
                    f.seek(size - 16, 1)
                else:
                    f.seek(size - 8, 1)
                scanned += size
    except Exception:
        pass
    return False


def _ensure_faststart(file_path: Path) -> Path:
    """
    如果 MP4 非 faststart，用 ffmpeg remux 到 .faststart.mp4 缓存文件。
    只 copy stream 不重编码，耗时极短。返回可直接服务的文件路径。
    """
    if not file_path.suffix.lower() == ".mp4":
        return file_path

    if not _needs_faststart(file_path):
        return file_path

    cache_dir = get_faststart_cache_dir()
    cache_path = cache_dir / f"{file_path.stem}.faststart.mp4"
    if cache_path.is_file() and cache_path.stat().st_mtime >= file_path.stat().st_mtime:
        return cache_path

    logger.info("Remuxing %s → faststart", file_path.name)
    try:
        # 写到临时文件再 rename，保证原子性
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".mp4", dir=str(file_path.parent)
        )
        import os
        os.close(tmp_fd)

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(file_path),
                "-c", "copy",
                "-movflags", "+faststart",
                tmp_path,
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg faststart failed: %s", result.stderr.decode()[-500:])
            Path(tmp_path).unlink(missing_ok=True)
            return file_path

        Path(tmp_path).rename(cache_path)
        logger.info("Faststart remux done: %s", cache_path.name)
        return cache_path
    except Exception as e:
        logger.warning("Faststart remux error: %s", e)
        return file_path


@router.get("/media/{path:path}")
async def serve_media(request: Request, path: str):
    """
    静态文件服务，支持 Range header（视频 seek）。

    Multi-root lookup under data_dir:
      1. workdir/{path}
      2. uploads/{path}
      3. gcs/{path}
    """
    data_dir: Path = request.app.state.data_dir

    # Try multiple roots
    file_path = None
    for sub in ("dub", "uploads", "gcs"):
        candidate = (data_dir / sub / path).resolve()
        # Security: resolved must be under data_dir
        if not str(candidate).startswith(str(data_dir.resolve())):
            continue
        if candidate.is_file():
            file_path = candidate
            break

    # 4. GCS download fallback
    if file_path is None:
        try:
            from dubora_core.utils.file_store import _gcs_bucket
            gcs_local = get_gcs_cache_dir() / path
            blob = _gcs_bucket().blob(path)
            if blob.exists():
                gcs_local.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(gcs_local))
                logger.info("Downloaded from GCS: %s", path)
                file_path = gcs_local
        except Exception as e:
            logger.warning("GCS download failed for %s: %s", path, e)

    if file_path is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # 对 MP4 自动 faststart
    file_path = _ensure_faststart(file_path)

    # 检测 MIME 类型
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        # 解析 Range header
        start, end = _parse_range(range_header, file_size)
        if start is None:
            raise HTTPException(status_code=416, detail="Invalid range")

        content_length = end - start + 1

        def iter_file():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(1024 * 1024, remaining)  # 1MB chunks
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_file(),
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Content-Type": mime_type,
            },
        )

    # 不带 Range 的完整文件响应
    return FileResponse(
        path=str(file_path),
        media_type=mime_type,
        headers={
            "Accept-Ranges": "bytes",
        },
    )


def _parse_range(range_header: str, file_size: int):
    """解析 Range header，返回 (start, end) 或 (None, None)。"""
    try:
        unit, ranges = range_header.split("=", 1)
        if unit.strip() != "bytes":
            return None, None
        range_spec = ranges.split(",")[0].strip()
        if range_spec.startswith("-"):
            # 后缀范围：-500 表示最后 500 字节
            suffix_length = int(range_spec[1:])
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        elif range_spec.endswith("-"):
            start = int(range_spec[:-1])
            end = file_size - 1
        else:
            parts = range_spec.split("-")
            start = int(parts[0])
            end = int(parts[1])
        if start > end or start >= file_size:
            return None, None
        end = min(end, file_size - 1)
        return start, end
    except (ValueError, IndexError):
        return None, None
