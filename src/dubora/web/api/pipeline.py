"""
Pipeline API: 状态查询 + 流式执行 + 阻塞式执行

dub.json 是 pipeline 唯一 SSOT，不再需要 export/sync/merge 中间步骤。
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from dubora.pipeline.phases import ALL_PHASES, GATES, STAGES

logger = logging.getLogger(__name__)

router = APIRouter()

# Phase metadata from backend registry
PHASES_META = [{"name": p.name, "label": p.label} for p in ALL_PHASES]

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
    """Read manifest.json and return phase status summary for all 8 phases."""
    manifest_path = workdir / "manifest.json"
    phases_data: dict = {}
    if manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        phases_data = data.get("phases", {})

    result = []
    for meta in PHASES_META:
        pd = phases_data.get(meta["name"])
        if pd:
            result.append({
                "name": meta["name"],
                "label": meta["label"],
                "status": pd.get("status", "pending"),
                "started_at": pd.get("started_at"),
                "finished_at": pd.get("finished_at"),
                "skipped": pd.get("skipped", False),
                "metrics": pd.get("metrics", {}),
                "error": pd.get("error"),
            })
        else:
            result.append({
                "name": meta["name"],
                "label": meta["label"],
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "skipped": False,
                "metrics": {},
                "error": None,
            })
    return result


def _derive_stages(phases: list[dict]) -> list[dict]:
    """Derive stage status from phase statuses."""
    phase_map = {p["name"]: p for p in phases}
    result = []
    for stage_def in STAGES:
        child_phases = [phase_map.get(pn) for pn in stage_def["phases"] if phase_map.get(pn)]
        if any(p["status"] == "failed" for p in child_phases):
            status = "failed"
        elif any(p["status"] == "running" for p in child_phases):
            status = "running"
        elif all(p["status"] in ("succeeded", "skipped") for p in child_phases):
            status = "succeeded"
        else:
            status = "pending"
        result.append({
            "key": stage_def["key"],
            "label": stage_def["label"],
            "phases": stage_def["phases"],
            "status": status,
        })
    return result


def _read_manifest_gates(workdir: Path) -> list[dict]:
    """Read manifest.json and return gate status for all gates."""
    manifest_path = workdir / "manifest.json"
    gates_data: dict = {}
    if manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        gates_data = data.get("gates", {})

    result = []
    for gate_def in GATES:
        gd = gates_data.get(gate_def["key"])
        result.append({
            "key": gate_def["key"],
            "after": gate_def["after"],
            "label": gate_def["label"],
            "status": gd.get("status", "pending") if gd else "pending",
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

    phases = _read_manifest_phases(workdir)
    return {
        "has_manifest": manifest_path.is_file(),
        "phases": phases,
        "gates": _read_manifest_gates(workdir),
        "stages": _derive_stages(phases),
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
    from_phase = body.get("from_phase")
    to_phase = body.get("to_phase")

    async def event_stream():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        # dub.json is the single SSOT - no export/sync/merge needed

        cmd = [
            sys.executable, "-m", "dubora.cli",
            "run", str(video_path),
        ]
        if from_phase:
            cmd += ["--from", from_phase]
        if to_phase:
            cmd += ["--to", to_phase]

        desc = f"--from {from_phase} --to {to_phase}" if from_phase or to_phase else "auto-advance"
        yield sse("log", {"line": f"Pipeline started: {desc}"})

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
                for meta in PHASES_META:
                    if meta["name"] in line_lower and ("phase" in line_lower or "running" in line_lower):
                        yield sse("phase", {"name": meta["name"]})
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
