"""
Artifact path resolution: deterministic key → workspace-relative path mapping.
"""
from pathlib import Path


def resolve_artifact_path(key: str, workspace: Path) -> Path:
    """
    根据 artifact key 解析最终文件路径。

    Args:
        key: artifact key（如 "extract.audio"）
        workspace: workspace 根目录

    Returns:
        最终文件路径（绝对路径）
    """
    parts = key.split(".", 1)
    if len(parts) == 2:
        domain, obj = parts
    else:
        domain = "misc"
        obj = key

    path_map = {
        "extract": {
            "audio": "{episode_stem}.wav",
            "vocals": "{episode_stem}-vocals.wav",
            "accompaniment": "{episode_stem}-accompaniment.wav",
        },
        "asr": {
            "doubao": "asr-doubao.json",
            "tencent": "asr-tencent.json",
            "gemini": "asr-gemini.json",
            "openai": "asr-openai.json",
            "fish": "asr-fish.json",
        },
        "subs": {
            "zh_srt": "output/{episode_stem}-zh.srt",
            "en_srt": "output/{episode_stem}-en.srt",
        },
        "tts": {
            "segments_dir": "tts/segments",
        },
        "mix": {
            "audio": "{episode_stem}-mix.wav",
        },
        "burn": {
            "video": "output/{episode_stem}-dubbed.mp4",
        },
    }

    if domain in path_map and obj in path_map[domain]:
        path_template = path_map[domain][obj]
    else:
        path_template = f"{domain}/{obj}"

    episode_stem = workspace.name
    path_str = path_template.format(episode_stem=episode_stem)

    return workspace / path_str
