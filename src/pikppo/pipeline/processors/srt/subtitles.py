"""
字幕生成器：根据 ASR 结果生成 segment.json 和 srt 文件

职责：
- 应用后处理策略生成 segments
- 生成 segment.json 文件
- 生成 srt 文件
- 检查缓存，避免重复生成

注意：
- 不依赖具体的 model 实现（通过函数参数传入）
"""
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pikppo.schema import Utterance, Segment
from pikppo.utils.logger import info, warning
from .srt import segments_to_srt_cues, write_srt


def segments_path(output_dir: Path, video_stem: str = None) -> Path:
    """
    获取 segments 文件路径（符合 pipeline 规范）。
    
    Pipeline 规范：
    - 如果 output_dir 是 workdir（xxxx/dub/<ep>/），使用 workdir/subs/zh-segments.json
    - 否则使用 output_dir/{video_stem}-segments.json（向后兼容测试工具）
    
    Args:
        output_dir: 输出目录（workdir 或自定义目录）
        video_stem: 视频文件名（可选，用于非标准路径）
    
    Returns:
        segments 文件路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 检查是否是 pipeline workdir（xxxx/dub/<ep>/ 结构）
    is_workdir = (
        output_dir.name.isdigit() and 
        output_dir.parent.name == "dub"
    )
    
    if is_workdir:
        # Pipeline 标准路径：workdir/subs/zh-segments.json
        subs_dir = output_dir / "subs"
        subs_dir.mkdir(parents=True, exist_ok=True)
        return subs_dir / "zh-segments.json"
    else:
        # 非标准路径：使用 video_stem（向后兼容测试工具）
        if video_stem:
            return output_dir / f"{video_stem}-segments.json"
        else:
            return output_dir / "segments.json"


def srt_path(output_dir: Path, video_stem: str = None) -> Path:
    """
    获取 SRT 文件路径（符合 pipeline 规范）。
    
    Pipeline 规范：
    - 如果 output_dir 是 workdir（xxxx/dub/<ep>/），使用 workdir/subs/zh.srt
    - 否则使用 output_dir/{video_stem}.srt（向后兼容测试工具）
    
    Args:
        output_dir: 输出目录（workdir 或自定义目录）
        video_stem: 视频文件名（可选，用于非标准路径）
    
    Returns:
        SRT 文件路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 检查是否是 pipeline workdir（xxxx/dub/<ep>/ 结构）
    is_workdir = (
        output_dir.name.isdigit() and 
        output_dir.parent.name == "dub"
    )
    
    if is_workdir:
        # Pipeline 标准路径：workdir/subs/zh.srt
        subs_dir = output_dir / "subs"
        subs_dir.mkdir(parents=True, exist_ok=True)
        return subs_dir / "zh.srt"
    else:
        # 非标准路径：使用 video_stem（向后兼容测试工具）
        if video_stem:
            return output_dir / f"{video_stem}.srt"
        else:
            return output_dir / "subtitle.srt"


def resolve_cached_subtitles(
    output_dir: Path,
    video_stem: str = None,
) -> Optional[Dict[str, Path]]:
    """
    解析缓存的字幕文件路径（如果存在）。
    
    Args:
        output_dir: 输出目录
        video_stem: 视频文件名（可选）
    
    Returns:
        如果存在缓存，返回 {"segments": segments_path, "srt": srt_path}，否则返回 None
    """
    segments_path_val = segments_path(output_dir, video_stem)
    srt_path_val = srt_path(output_dir, video_stem)
    
    if segments_path_val.exists() and srt_path_val.exists():
        # 检查文件是否非空
        if segments_path_val.stat().st_size > 0 and srt_path_val.stat().st_size > 0:
            return {
                "segments": segments_path_val,
                "srt": srt_path_val,
            }
    
    return None


def generate_subtitles(
    utterances: List[Utterance],
    postprocess_fn: Callable,
    postprofiles: Dict[str, Dict[str, Any]],
    postprofile: str,
    output_dir: Path,
    video_stem: str = None,
    use_cache: bool = True,
) -> Dict[str, Path]:
    """
    根据 utterances 和字幕策略生成字幕文件（符合 pipeline 规范）。
    
    如果 use_cache=True 且存在缓存，直接返回缓存文件路径，否则生成新文件。
    
    Pipeline 规范：
    - 如果 output_dir 是 workdir（xxxx/dub/<ep>/），生成 workdir/subs/zh-segments.json 和 workdir/subs/zh.srt
    - 否则使用 video_stem 生成自定义路径（向后兼容测试工具）
    
    Args:
        utterances: ASR 解析后的 utterances
        postprocess_fn: 后处理函数，接受 (utterances, profile_name=...) 返回 Segment[]
        postprofiles: 后处理策略配置字典
        postprofile: 字幕策略名称（用于后处理，不影响文件名）
        output_dir: 输出目录（workdir 或自定义目录）
        video_stem: 视频文件名（可选，用于非标准路径）
        use_cache: 是否使用缓存
    
    Returns:
        {"segments": segments_path, "srt": srt_path}
    
    Raises:
        KeyError: 如果 postprofile 不存在
    """
    # 检查缓存
    if use_cache:
        cached = resolve_cached_subtitles(output_dir, video_stem)
        if cached is not None:
            return cached
    
    # 验证 postprofile
    if postprofile not in postprofiles:
        raise KeyError(
            f"未知的字幕策略: {postprofile}\n"
            f"可用策略: {', '.join(sorted(postprofiles.keys()))}"
        )
    
    # 应用后处理策略
    info(f"应用字幕策略: {postprofile}")
    # 支持关键字参数调用（speaker_aware_postprocess 使用 profile_name=...）
    import inspect
    sig = inspect.signature(postprocess_fn)
    if 'profile_name' in sig.parameters:
        segments = postprocess_fn(utterances, profile_name=postprofile, profiles=postprofiles)
    else:
        # 向后兼容：位置参数
        segments = postprocess_fn(utterances, postprofile, postprofiles)
    
    if not segments:
        warning("后处理未生成任何 segments")
        segments = []
    
    # 生成文件路径
    segments_path_val = segments_path(output_dir, video_stem)
    srt_path_val = srt_path(output_dir, video_stem)
    
    # 保存 segments JSON（segments 已经在 asr_post.py 中清理过标点）
    output_dir.mkdir(parents=True, exist_ok=True)
    segments_dict = [
        {
            "start": seg.start_ms / 1000.0,  # 毫秒转秒
            "end": seg.end_ms / 1000.0,
            "text": seg.text,  # 已经在 asr_post.py 中清理过标点
            "speaker": seg.speaker if hasattr(seg, "speaker") else None,
        }
        for seg in segments
    ]
    
    with open(segments_path_val, "w", encoding="utf-8") as f:
        json.dump(segments_dict, f, indent=2, ensure_ascii=False)
    
    info(f"Saved segments to: {segments_path_val}")
    
    # 生成 SRT 文件（使用 srt.py 的函数）
    # segments 已经在 asr_post.py 中清理过标点，直接使用即可
    srt_cues = segments_to_srt_cues(segments)  # Segment[] → SrtCue[]
    write_srt(srt_path_val, srt_cues)  # SrtCue[] → SRT 文件
    info(f"Saved SRT to: {srt_path_val}")
    
    return {
        "segments": segments_path_val,
        "srt": srt_path_val,
    }


def generate_subtitles_from_preset(
    preset: str,
    postprofile: str,
    audio_url: str,
    output_dir: Path,
    video_stem: Optional[str] = None,
    appid: Optional[str] = None,
    access_token: Optional[str] = None,
    hotwords: Optional[List[str]] = None,
    use_cache: bool = True,
) -> Dict[str, Path]:
    """
    一站式生成字幕：从预设名称到最终文件（符合 pipeline 规范）。
    
    流程：
    1. 检查是否有缓存的 ASR 结果，如果没有则调用 API
    2. 检查是否有缓存的字幕文件，如果没有则生成
    
    Pipeline 规范：
    - 如果 output_dir 是 workdir（xxxx/dub/<ep>/），生成 workdir/subs/zh-segments.json 和 workdir/subs/zh.srt
    - 否则使用 video_stem 生成自定义路径（向后兼容测试工具）
    
    Args:
        preset: ASR 预设名称（用于调试，不影响文件名）
        postprofile: 字幕策略名称（用于后处理，不影响文件名）
        audio_url: 音频文件 URL
        output_dir: 输出目录（workdir 或自定义目录）
        video_stem: 视频文件名（可选，用于非标准路径）
        appid: 应用标识（可选，从环境变量读取）
        access_token: 访问令牌（可选，从环境变量读取）
        hotwords: 热词列表（可选）
        use_cache: 是否使用缓存（暂未实现，保留接口）
    
    Returns:
        {"segments": segments_path, "srt": srt_path}
    """
    # 导入 transcribe 函数
    from pikppo.pipeline.processors.asr.transcribe import transcribe
    
    # 导入后处理函数和配置（从外部传入，pipeline 不直接依赖）
    from .profiles import POSTPROFILES
    from .asr_post import speaker_aware_postprocess
    
    # 1. 调用 ASR 服务
    _, utterances = transcribe(
        audio_url=audio_url,
        preset=preset,
        appid=appid,
        access_token=access_token,
        hotwords=hotwords,
    )
    
    # 2. 生成字幕文件（带缓存检查）- 符合 pipeline 规范
    return generate_subtitles(
        utterances=utterances,
        postprocess_fn=speaker_aware_postprocess,
        postprofiles=POSTPROFILES,
        postprofile=postprofile,
        output_dir=output_dir,
        video_stem=video_stem,
        use_cache=use_cache,
    )
