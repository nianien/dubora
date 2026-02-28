"""
Export API: 导出 subtitle.model.json + zh.srt
"""
import json
from pathlib import Path

import srt
from datetime import timedelta
from fastapi import APIRouter, HTTPException, Request

from dubora.schema.asr_model import AsrModel
from dubora.pipeline.core.atomic import atomic_write
from dubora.pipeline.processors.voiceprint.speaker_to_role import _load_roles
from dubora.web.converters.export_subtitle import export_subtitle_model

router = APIRouter()


def do_export(videos_dir: Path, drama: str, ep: str) -> dict:
    """
    导出 dub.json → subtitle.model.json + zh.srt。

    Returns:
        {"status": "ok", "exported": [...], "segments": N, "utterances": N}

    Raises:
        FileNotFoundError: dub.json 不存在
    """
    workdir = videos_dir / drama / "dub" / ep
    state_dir = workdir / "state"
    output_dir = workdir / "output"

    model_path = state_dir / "dub.json"
    if not model_path.is_file():
        raise FileNotFoundError("dub.json not found")

    with open(model_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    asr_model = AsrModel.from_dict(data)

    # 加载 roles 用于 gender 查询
    roles_path = videos_dir / drama / "dub" / "dict" / "roles.json"
    roles_data = _load_roles(str(roles_path))

    exported_files = []

    # 1. Export subtitle.model.json
    sub_model = export_subtitle_model(asr_model, roles_data=roles_data)
    sub_dict = _subtitle_model_to_dict(sub_model)
    sub_path = state_dir / "subtitle.model.json"
    atomic_write(
        json.dumps(sub_dict, indent=2, ensure_ascii=False),
        sub_path,
    )
    exported_files.append(str(sub_path.relative_to(workdir)))

    # 2. Export zh.srt
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_content = _build_zh_srt(sub_model)
    srt_path = output_dir / "zh.srt"
    atomic_write(srt_content, srt_path)
    exported_files.append(str(srt_path.relative_to(workdir)))

    return {
        "status": "ok",
        "exported": exported_files,
        "segments": len(asr_model.segments),
        "utterances": len(sub_model.utterances),
    }


@router.post("/episodes/{drama}/{ep}/export")
async def export_episode(request: Request, drama: str, ep: str) -> dict:
    """导出 dub.json → subtitle.model.json + zh.srt。"""
    videos_dir: Path = request.app.state.videos_dir
    try:
        return do_export(videos_dir, drama, ep)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _subtitle_model_to_dict(model) -> dict:
    """将 SubtitleModel 序列化为字典。"""
    utterances = []
    for utt in model.utterances:
        cues = []
        for cue in utt.cues:
            cues.append({
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "source": {
                    "lang": cue.source.lang,
                    "text": cue.source.text,
                },
            })
        speaker_dict = {
            "id": utt.speaker.id,
            "gender": utt.speaker.gender,
        }
        if utt.speaker.emotion:
            speaker_dict["emotion"] = {
                "label": utt.speaker.emotion.label,
            }
            if utt.speaker.emotion.confidence is not None:
                speaker_dict["emotion"]["confidence"] = utt.speaker.emotion.confidence
            if utt.speaker.emotion.intensity is not None:
                speaker_dict["emotion"]["intensity"] = utt.speaker.emotion.intensity

        utterances.append({
            "utt_id": utt.utt_id,
            "speaker": speaker_dict,
            "start_ms": utt.start_ms,
            "end_ms": utt.end_ms,
            "cues": cues,
        })

    result = {
        "schema": {
            "name": model.schema.name,
            "version": model.schema.version,
        },
        "utterances": utterances,
    }
    if model.audio:
        result["audio"] = model.audio
    return result


def _build_zh_srt(model) -> str:
    """从 SubtitleModel 构建中文 SRT 字幕。"""
    subs = []
    index = 1
    for utt in model.utterances:
        for cue in utt.cues:
            subs.append(srt.Subtitle(
                index=index,
                start=timedelta(milliseconds=cue.start_ms),
                end=timedelta(milliseconds=cue.end_ms),
                content=cue.source.text,
            ))
            index += 1
    return srt.compose(subs)
