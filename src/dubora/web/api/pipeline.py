"""
Pipeline API: 状态查询 + 流式执行 + 阻塞式执行

dub.json 是 pipeline 唯一 SSOT，不再需要 export/sync/merge 中间步骤。
"""
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# 9-phase ordered list
PHASE_NAMES = ["demux", "sep", "asr", "sub", "mt", "align", "tts", "mix", "burn"]

# In-memory lock: one running pipeline per episode
_running: dict[str, asyncio.subprocess.Process] = {}


def _workdir(videos_dir: Path, drama: str, ep: str) -> Path:
    return videos_dir / drama / "dub" / ep


def _find_video(videos_dir: Path, drama: str, ep: str) -> Optional[Path]:
    for ext in (".mp4", ".mkv", ".avi"):
        vf = videos_dir / drama / f"{ep}{ext}"
        if vf.is_file():
            return vf
    return None


def _read_manifest_phases(workdir: Path) -> list[dict]:
    """Read manifest.json and return phase status summary for all 9 phases."""
    manifest_path = workdir / "manifest.json"
    phases_data: dict = {}
    if manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        phases_data = data.get("phases", {})

    result = []
    for name in PHASE_NAMES:
        pd = phases_data.get(name)
        if pd:
            result.append({
                "name": name,
                "status": pd.get("status", "pending"),
                "started_at": pd.get("started_at"),
                "finished_at": pd.get("finished_at"),
                "skipped": pd.get("skipped", False),
                "metrics": pd.get("metrics", {}),
                "error": pd.get("error"),
            })
        else:
            result.append({
                "name": name,
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "skipped": False,
                "metrics": {},
                "error": None,
            })
    return result


# --------------------------------------------------------------------------- #
# GET /episodes/{drama}/{ep}/pipeline/status
# --------------------------------------------------------------------------- #

@router.get("/episodes/{drama}/{ep}/pipeline/status")
async def pipeline_status(request: Request, drama: str, ep: str) -> dict:
    """Return phase status summary from manifest.json."""
    videos_dir: Path = request.app.state.videos_dir
    workdir = _workdir(videos_dir, drama, ep)
    manifest_path = workdir / "manifest.json"

    return {
        "has_manifest": manifest_path.is_file(),
        "phases": _read_manifest_phases(workdir),
    }


# --------------------------------------------------------------------------- #
# POST /episodes/{drama}/{ep}/pipeline/run-stream  (SSE)
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/run-stream")
async def run_pipeline_stream(request: Request, drama: str, ep: str):
    """
    SSE streaming pipeline execution.

    Request body:
        {"from_phase": "mt", "to_phase": "burn"}

    SSE events:
        event: log     data: {"line": "..."}
        event: phase   data: {"name": "mt"}
        event: done    data: {"returncode": 0}
        event: error   data: {"message": "..."}
    """
    videos_dir: Path = request.app.state.videos_dir

    # Concurrency guard
    lock_key = f"{drama}/{ep}"
    if lock_key in _running:
        raise HTTPException(status_code=409, detail="Pipeline already running for this episode")

    video_path = _find_video(videos_dir, drama, ep)
    if not video_path:
        raise HTTPException(status_code=404, detail=f"Video file not found for {drama}/{ep}")

    body = await request.json() if await request.body() else {}
    from_phase = body.get("from_phase", "mt")
    to_phase = body.get("to_phase", "burn")

    async def event_stream():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        # dub.json is the single SSOT - no export/sync/merge needed

        cmd = [
            sys.executable, "-m", "dubora.cli",
            "run", str(video_path),
            "--from", from_phase,
            "--to", to_phase,
        ]

        yield sse("log", {"line": f"Pipeline started: --from {from_phase} --to {to_phase}"})

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            _running[lock_key] = proc

            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue

                yield sse("log", {"line": line})

                # Detect phase transitions from log output
                line_lower = line.lower()
                for pname in PHASE_NAMES:
                    if pname in line_lower and ("phase" in line_lower or "running" in line_lower):
                        yield sse("phase", {"name": pname})
                        break

            returncode = await proc.wait()
            yield sse("done", {"returncode": returncode})

        except asyncio.CancelledError:
            if lock_key in _running:
                _running[lock_key].kill()
            yield sse("error", {"message": "Pipeline cancelled"})
        except Exception as e:
            yield sse("error", {"message": str(e)})
        finally:
            _running.pop(lock_key, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# Deprecated endpoints (kept to avoid frontend 404s)
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/merge-translations")
async def merge_translations_endpoint(request: Request, drama: str, ep: str) -> dict:
    """Deprecated: dub.json is now the single SSOT, no merge needed."""
    return {"status": "ok", "updated": 0, "deprecated": True}


@router.post("/episodes/{drama}/{ep}/pipeline/sync-translations")
async def sync_translations_endpoint(request: Request, drama: str, ep: str) -> dict:
    """Deprecated: dub.json is now the single SSOT, no sync needed."""
    return {"status": "ok", "updated": 0, "deprecated": True}


# --------------------------------------------------------------------------- #
# POST /episodes/{drama}/{ep}/pipeline/cancel
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/cancel")
async def cancel_pipeline(request: Request, drama: str, ep: str) -> dict:
    """Cancel a running pipeline for this episode."""
    lock_key = f"{drama}/{ep}"
    proc = _running.get(lock_key)
    if proc is None:
        raise HTTPException(status_code=404, detail="No running pipeline for this episode")

    try:
        proc.kill()
    except ProcessLookupError:
        pass
    _running.pop(lock_key, None)

    return {"status": "cancelled"}


# --------------------------------------------------------------------------- #
# POST /episodes/{drama}/{ep}/pipeline/run  (legacy blocking)
# --------------------------------------------------------------------------- #

@router.post("/episodes/{drama}/{ep}/pipeline/run")
async def run_pipeline(request: Request, drama: str, ep: str) -> dict:
    """
    Legacy blocking pipeline execution (subprocess).
    Kept for backward compatibility.
    """
    videos_dir: Path = request.app.state.videos_dir

    video_path = _find_video(videos_dir, drama, ep)
    if not video_path:
        raise HTTPException(
            status_code=404,
            detail=f"Video file not found for {drama}/{ep}",
        )

    body = await request.json() if await request.body() else {}
    from_phase = body.get("from_phase", "mt")
    to_phase = body.get("to_phase", "burn")

    cmd = [
        sys.executable, "-m", "dubora.cli",
        "run", str(video_path),
        "--from", from_phase,
        "--to", to_phase,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "returncode": -1,
            "stdout": "",
            "stderr": "Pipeline execution timed out (10 min)",
        }
