"""
CLI entry point for dubora pipeline (Pipeline Framework v1)

支持批量操作：
  vsd run videos/drama/[4-70].mp4 --to burn
  vsd bless videos/drama/[1-10].mp4 sub
  vsd fix videos/drama/[1-10].mp4 asr
"""
import argparse
import re
import sys
import uuid
from pathlib import Path
from typing import List, Optional

from dubora.config.settings import PipelineConfig, load_env_file
from dubora.pipeline.phases import ALL_PHASES, build_phases
from dubora.pipeline.core.runner import PhaseRunner
from dubora.pipeline.core.manifest import Manifest
from dubora.pipeline.core.types import RunContext
from dubora.utils.logger import info, error, success


def get_workdir(video_path: Path, output_dir: Optional[Path] = None) -> Path:
    """
    根据 video_path 确定 workdir。

    规则：
    - 如果视频在 {任意路径}/abc/{file}.mp4，则输出在 {相同路径}/abc/dub/{file_stem}/
    - 例如：videos/dbqsfy/1.mp4 → videos/dbqsfy/dub/1/
    """
    video_path = Path(video_path).resolve()
    parent_dir = video_path.parent
    video_stem = video_path.stem
    workdir = parent_dir / "dub" / video_stem
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def config_to_dict(config: PipelineConfig) -> dict:
    """将 PipelineConfig 转换为 dict，用于 RunContext。"""
    return {
        "video_path": None,
        "doubao_asr_preset": config.doubao_asr_preset,
        "doubao_postprofile": config.doubao_postprofile,
        "doubao_hotwords": config.doubao_hotwords,
        "openai_model": config.openai_model,
        "openai_temperature": config.openai_temperature,
        "azure_tts_key": config.azure_tts_key,
        "azure_tts_region": config.azure_tts_region,
        "azure_tts_language": config.azure_tts_language,
        "tts_engine": config.tts_engine,
        "tts_max_workers": config.tts_max_workers,
        "tts_mute_original": config.tts_mute_original,
        "voice_pool_path": config.voice_pool_path,
        "dub_target_lufs": config.dub_target_lufs,
        "dub_true_peak": config.dub_true_peak,
        "phases": {},
    }


def expand_video_pattern(pattern: str) -> List[Path]:
    """
    展开视频路径中的范围模式。

    支持格式：
    - videos/drama/4-70.mp4    → 4.mp4, 5.mp4, ..., 70.mp4
    - videos/drama/1.mp4       → 1.mp4（单文件，原样返回）

    只返回实际存在的文件，按数字升序排列。
    """
    # 匹配文件名部分的 N-M 模式（如 /path/4-70.mp4）
    # 提取文件名部分再匹配，避免路径中的数字误匹配
    basename = pattern.rsplit("/", 1)[-1] if "/" in pattern else pattern
    m = re.match(r'(\d+)-(\d+)(\.)', basename)
    if not m:
        p = Path(pattern)
        return [p] if p.exists() else []

    start, end = int(m.group(1)), int(m.group(2))
    # prefix = 目录部分, suffix = 扩展名部分（含点）
    dir_part = pattern.rsplit("/", 1)[0] + "/" if "/" in pattern else ""
    ext_part = basename[m.start(3):]  # 从 "." 开始

    paths = []
    for i in range(start, end + 1):
        p = Path(f"{dir_part}{i}{ext_part}")
        if p.exists():
            paths.append(p)

    paths.sort(key=lambda p: int(m2.group(1)) if (m2 := re.search(r'(\d+)', p.stem)) else 0)
    return paths


# ── 单文件操作 ──────────────────────────────────────────────

def run_one(video_path: Path, args, config: PipelineConfig):
    """对单个视频执行 pipeline run。"""
    workdir = get_workdir(video_path, Path(args.output_dir))
    manifest_path = workdir / "manifest.json"
    manifest = Manifest(manifest_path)

    job_id = str(uuid.uuid4())
    manifest.set_job(job_id, str(workdir))
    manifest.save()

    config_dict = config_to_dict(config)
    config_dict["video_path"] = str(video_path.absolute())

    ctx = RunContext(
        job_id=job_id,
        workspace=str(workdir),
        config=config_dict,
    )

    phases = build_phases(config)
    runner = PhaseRunner(manifest, workdir)
    outputs = runner.run_pipeline(
        phases=phases,
        ctx=ctx,
        to_phase=args.to,
        from_phase=args.from_phase,
    )

    success(f"[{video_path.name}] Pipeline completed")
    for key, path in outputs.items():
        info(f"  {key}: {path}")


def bless_one(video_path: Path, phase_name: str):
    """对单个视频执行 bless。"""
    workdir = get_workdir(video_path)
    manifest_path = workdir / "manifest.json"
    if not manifest_path.exists():
        error(f"[{video_path.name}] Manifest not found: {manifest_path}")
        return False

    manifest = Manifest(manifest_path)
    phase_data = manifest.get_phase_data(phase_name)
    if phase_data is None:
        error(f"[{video_path.name}] Phase '{phase_name}' has no record in manifest")
        return False

    phase_artifacts = phase_data.get("artifacts", {})
    if not phase_artifacts:
        error(f"[{video_path.name}] Phase '{phase_name}' has no output artifacts")
        return False

    from dubora.pipeline.core.fingerprints import hash_path

    updated = 0
    for key, artifact_data in phase_artifacts.items():
        relpath = artifact_data.get("relpath")
        if not relpath:
            continue
        artifact_path = workdir / relpath
        if not artifact_path.exists():
            error(f"  {key}: file not found ({artifact_path})")
            continue

        old_fp = artifact_data.get("fingerprint", "")
        new_fp = hash_path(artifact_path)
        if old_fp == new_fp:
            continue

        artifact_data["fingerprint"] = new_fp
        if key in manifest.data["artifacts"]:
            manifest.data["artifacts"][key]["fingerprint"] = new_fp
        updated += 1
        info(f"  {key}: {old_fp[:16]}... -> {new_fp[:16]}...")

    if updated:
        manifest.save()
        success(f"[{video_path.name}] Blessed {updated} artifact(s) for phase '{phase_name}'")
    else:
        info(f"[{video_path.name}] All artifacts for phase '{phase_name}' are unchanged")
    return True


def fix_one(video_path: Path, phase_name: str):
    """对单个视频执行 fix。"""
    workdir = get_workdir(video_path)

    if phase_name == "asr":
        result_path = workdir / "source" / "asr-result.json"
        if not result_path.exists():
            error(f"[{video_path.name}] asr-result.json not found: {result_path}")
            return False

        import json
        from dubora.schema.asr_fix import AsrFix

        with open(result_path, "r", encoding="utf-8") as f:
            raw_response = json.load(f)

        asr_fix = AsrFix.from_raw_response(raw_response)
        fix_path = workdir / "source" / "asr.fix.json"
        with open(fix_path, "w", encoding="utf-8") as f:
            json.dump(asr_fix.to_dict(), f, indent=2, ensure_ascii=False)

        success(f"[{video_path.name}] Regenerated {fix_path}")
        return True
    else:
        error(f"Phase '{phase_name}' does not support fix")
        return False


# ── 主入口 ──────────────────────────────────────────────────

def main():
    """Main CLI entry point"""
    phase_names = [phase.name for phase in ALL_PHASES]

    parser = argparse.ArgumentParser(
        description="Video dubbing pipeline with phase-based execution (Framework v1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Phases: {' -> '.join(phase_names)}

Examples:
  vsd run video.mp4 --to asr                          # Single file
  vsd run videos/drama/4-70.mp4 --to burn             # Batch: episodes 4-70
  vsd run videos/drama/1-10.mp4 --from mt --to tts    # Batch: re-run MT to TTS
  vsd bless videos/drama/1-10.mp4 sub                 # Batch bless
  vsd fix videos/drama/1-10.mp4 asr                   # Batch fix
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # run command
    run_parser = subparsers.add_parser("run", help="Run pipeline phases")
    run_parser.add_argument("video", type=str, help="Input video file path (supports N-M range, e.g. 4-70.mp4)")
    run_parser.add_argument(
        "--to", type=str, required=True, choices=phase_names,
        help="Target phase to run up to",
    )
    run_parser.add_argument(
        "--from", type=str, dest="from_phase", choices=phase_names,
        help="Force refresh from this phase (inclusive)",
    )
    run_parser.add_argument(
        "--output-dir", type=str, default="runs",
        help="Output directory (default: runs)",
    )
    run_parser.add_argument(
        "--config", type=str, help="Path to config file (optional)",
    )

    # bless command
    bless_parser = subparsers.add_parser(
        "bless", help="Accept manual edits: re-fingerprint a phase's output artifacts",
    )
    bless_parser.add_argument("video", type=str, help="Input video file path (supports N-M range, e.g. 4-70.mp4)")
    bless_parser.add_argument("phase", type=str, choices=phase_names, help="Phase whose outputs to re-fingerprint")

    # fix command
    fix_parser = subparsers.add_parser(
        "fix", help="Regenerate human-editable fix files from source",
    )
    fix_parser.add_argument("video", type=str, help="Input video file path (supports N-M range, e.g. 4-70.mp4)")
    fix_parser.add_argument("phase", type=str, choices=phase_names, help="Phase whose fix file to regenerate")

    # phases command
    subparsers.add_parser("phases", help="List available phases")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    load_env_file()

    if args.command == "phases":
        info("Available phases:")
        for phase in ALL_PHASES:
            info(f"  - {phase.name} (v{phase.version}): requires={phase.requires()}, provides={phase.provides()}")
        return

    # ── 展开批量模式 ──
    video_paths = expand_video_pattern(args.video)
    if not video_paths:
        error(f"No video files found matching: {args.video}")
        sys.exit(1)

    is_batch = len(video_paths) > 1
    if is_batch:
        info(f"Batch mode: {len(video_paths)} files matched")

    # ── 执行命令 ──
    failed = []

    if args.command == "run":
        config = PipelineConfig()
        if args.config:
            pass  # TODO: Load from config file if needed

        for i, video_path in enumerate(video_paths):
            if is_batch:
                info(f"--- [{i+1}/{len(video_paths)}] {video_path.name} ---")
            try:
                run_one(video_path, args, config)
            except Exception as e:
                error(f"[{video_path.name}] Pipeline failed: {e}")
                if not is_batch:
                    import traceback
                    traceback.print_exc()
                    sys.exit(1)
                failed.append(video_path.name)

    elif args.command == "bless":
        for i, video_path in enumerate(video_paths):
            if is_batch:
                info(f"--- [{i+1}/{len(video_paths)}] {video_path.name} ---")
            if not bless_one(video_path, args.phase):
                failed.append(video_path.name)

    elif args.command == "fix":
        for i, video_path in enumerate(video_paths):
            if is_batch:
                info(f"--- [{i+1}/{len(video_paths)}] {video_path.name} ---")
            if not fix_one(video_path, args.phase):
                failed.append(video_path.name)

    # ── 批量汇总 ──
    if is_batch:
        ok_count = len(video_paths) - len(failed)
        info(f"Batch complete: {ok_count}/{len(video_paths)} succeeded")
        if failed:
            error(f"Failed: {', '.join(failed)}")
            sys.exit(1)


if __name__ == "__main__":
    main()
