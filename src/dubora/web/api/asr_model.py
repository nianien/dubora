"""
ASR Model API: GET/PUT asr-model
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from dubora.schema.asr_model import AsrModel
from dubora.pipeline.core.atomic import atomic_write
from dubora.web.converters.import_asr import import_asr_result

router = APIRouter()


def _get_workdir(request: Request, drama: str, ep: str) -> Path:
    """获取 episode 的工作目录。"""
    videos_dir: Path = request.app.state.videos_dir
    workdir = videos_dir / drama / "dub" / ep
    if not workdir.is_dir():
        raise HTTPException(status_code=404, detail=f"Episode not found: {drama}/{ep}")
    return workdir


@router.get("/episodes/{drama}/{ep}/asr-model")
async def get_asr_model(request: Request, drama: str, ep: str) -> dict:
    """
    获取 dub.json。

    如果不存在，自动从 asr-result.json 导入并创建。
    """
    workdir = _get_workdir(request, drama, ep)
    state_dir = workdir / "state"
    model_path = state_dir / "dub.json"

    if model_path.is_file():
        with open(model_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    # 自动从 asr-result.json 导入
    input_dir = workdir / "input"
    asr_result_path = input_dir / "asr-result.json"
    if not asr_result_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Neither dub.json nor asr-result.json found for {drama}/{ep}",
        )

    with open(asr_result_path, "r", encoding="utf-8") as f:
        raw_response = json.load(f)

    # 查找视频文件名
    video_filename = ""
    for ext in (".mp4", ".mkv", ".avi"):
        vf = workdir.parent.parent / f"{ep}{ext}"
        if vf.is_file():
            video_filename = vf.name
            break

    model = import_asr_result(
        raw_response,
        video_filename=video_filename,
    )

    # 保存（原子写入）
    content = json.dumps(model.to_dict(), indent=2, ensure_ascii=False)
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(content, model_path)

    return model.to_dict()


@router.put("/episodes/{drama}/{ep}/asr-model")
async def put_asr_model(request: Request, drama: str, ep: str) -> dict:
    """
    保存 dub.json（rev+1, 重算 fingerprint, atomic write）。
    """
    workdir = _get_workdir(request, drama, ep)
    state_dir = workdir / "state"
    model_path = state_dir / "dub.json"

    body = await request.json()
    model = AsrModel.from_dict(body)

    # bump rev + 重算 fingerprint
    model.bump_rev()
    model.detect_overlaps()
    model.update_fingerprint()

    content = json.dumps(model.to_dict(), indent=2, ensure_ascii=False)
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(content, model_path)

    return model.to_dict()
