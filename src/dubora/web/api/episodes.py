"""
Episodes API: 扫描 videos/ 目录，返回可用剧集列表
"""
from pathlib import Path
from typing import List

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/episodes")
async def list_episodes(request: Request) -> List[dict]:
    """
    扫描 videos/ 目录，返回可用剧集列表。

    返回格式：
    [
      {
        "drama": "东北雀神风云",
        "episode": "2",
        "has_asr_result": true,
        "has_asr_model": false,
        "has_subtitle_model": false
      }
    ]
    """
    videos_dir: Path = request.app.state.videos_dir
    if not videos_dir.is_dir():
        return []

    episodes = []
    for drama_dir in sorted(videos_dir.iterdir()):
        if not drama_dir.is_dir():
            continue

        # 收集已知 episode 名（从 dub 目录 + 视频文件两个来源合并）
        known_eps: dict[str, dict] = {}

        # 来源 1: dub/{episode}/ 目录
        dub_dir = drama_dir / "dub"
        if dub_dir.is_dir():
            for ep_dir in dub_dir.iterdir():
                if ep_dir.is_dir() and ep_dir.name != "dict":
                    known_eps[ep_dir.name] = {"has_dub_dir": True}

        # 来源 2: 视频文件 {drama}/{name}.mp4
        for vf in drama_dir.iterdir():
            if vf.is_file() and vf.suffix in (".mp4", ".mkv", ".avi"):
                ep_name = vf.stem
                if ep_name not in known_eps:
                    known_eps[ep_name] = {"has_dub_dir": False}

        # 按数字排序
        for ep_name in sorted(known_eps, key=_numeric_sort_key):
            ep_workdir = dub_dir / ep_name if dub_dir.is_dir() else drama_dir / "dub" / ep_name
            input_dir = ep_workdir / "input"
            state_dir = ep_workdir / "state"

            # 查找视频文件
            video_file = ""
            for ext in (".mp4", ".mkv", ".avi"):
                vf = drama_dir / f"{ep_name}{ext}"
                if vf.is_file():
                    video_file = f"{drama_dir.name}/{ep_name}{ext}"
                    break

            episodes.append({
                "drama": drama_dir.name,
                "episode": ep_name,
                "video_file": video_file,
                "has_asr_result": input_dir.is_dir() and (input_dir / "asr-result.json").is_file(),
                "has_asr_model": state_dir.is_dir() and (state_dir / "dub.json").is_file(),
                "has_subtitle_model": state_dir.is_dir() and (state_dir / "subtitle.model.json").is_file(),
            })

    return episodes


def _numeric_sort_key(name: str) -> tuple:
    """数字排序辅助：'2' → (2, ''), '10a' → (10, 'a')"""
    import re
    m = re.match(r'^(\d+)(.*)', name)
    if m:
        return (int(m.group(1)), m.group(2))
    return (float('inf'), name)
