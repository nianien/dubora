"""
CLI entry point for pikppo pipeline (Pipeline Framework v1)
"""
import argparse
import sys
import uuid
from pathlib import Path
from typing import Optional

from pikppo.config.settings import PipelineConfig, load_env_file
from pikppo.pipeline.phases import ALL_PHASES
from pikppo.pipeline.core.runner import PhaseRunner
from pikppo.pipeline.core.manifest import Manifest
from pikppo.pipeline.core.types import RunContext
from pikppo.utils.logger import info, error, success


def get_workdir(video_path: Path, output_dir: Optional[Path] = None) -> Path:
    """
    根据 video_path 确定 workdir。
    
    规则：
    - 如果视频在 {任意路径}/abc/{file}.mp4，则输出在 {相同路径}/abc/dub/{file_stem}/
    - 例如：videos/dbqsfy/1.mp4 → videos/dbqsfy/dub/1/
    - 例如：some/path/abc/video.mp4 → some/path/abc/dub/video/
    """
    video_path = Path(video_path).resolve()
    
    # 获取视频文件的父目录和文件名
    parent_dir = video_path.parent  # 例如：videos/dbqsfy 或 some/path/abc
    video_stem = video_path.stem    # 例如：1 或 video
    
    # workdir = {parent_dir}/dub/{video_stem}/
    workdir = parent_dir / "dub" / video_stem
    
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def config_to_dict(config: PipelineConfig) -> dict:
    """
    将 PipelineConfig 转换为 dict，用于 RunContext。
    """
    # 基本配置
    config_dict = {
        "video_path": None,  # 会在 CLI 中设置
        "doubao_asr_preset": config.doubao_asr_preset,
        "doubao_postprofile": config.doubao_postprofile,
        "doubao_hotwords": config.doubao_hotwords,
        "openai_model": config.openai_model,
        "openai_temperature": config.openai_temperature,
        "azure_tts_key": config.azure_tts_key,
        "azure_tts_region": config.azure_tts_region,
        "azure_tts_language": config.azure_tts_language,
        "tts_max_workers": config.tts_max_workers,
        "voice_pool_path": config.voice_pool_path,
        "dub_target_lufs": config.dub_target_lufs,
        "dub_true_peak": config.dub_true_peak,
        "phases": {},  # Phase-specific configs can go here
    }
    
    return config_dict


def main():
    """Main CLI entry point"""
    phase_names = [phase.name for phase in ALL_PHASES]
    
    parser = argparse.ArgumentParser(
        description="Video dubbing pipeline with phase-based execution (Framework v1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Phases: {' -> '.join(phase_names)}

Examples:
  vsd run video.mp4 --to asr     # Run up to ASR phase
  vsd run video.mp4 --to burn    # Run complete pipeline
  vsd run video.mp4 --from tts --to burn  # Force refresh from TTS to burn
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # run command
    run_parser = subparsers.add_parser("run", help="Run pipeline phases")
    run_parser.add_argument("video", type=str, help="Input video file path")
    run_parser.add_argument(
        "--to",
        type=str,
        required=True,
        choices=phase_names,
        help="Target phase to run up to"
    )
    run_parser.add_argument(
        "--from",
        type=str,
        dest="from_phase",
        choices=phase_names,
        help="Force refresh from this phase (inclusive)"
    )
    run_parser.add_argument(
        "--output-dir",
        type=str,
        default="runs",
        help="Output directory (default: runs)"
    )
    run_parser.add_argument(
        "--config",
        type=str,
        help="Path to config file (optional)"
    )

    # phases command
    subparsers.add_parser("phases", help="List available phases")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Load environment variables
    load_env_file()

    if args.command == "phases":
        info("Available phases:")
        for phase in ALL_PHASES:
            info(f"  - {phase.name} (v{phase.version}): requires={phase.requires()}, provides={phase.provides()}")
        return

    if args.command == "run":
        video_path = Path(args.video)
        if not video_path.exists():
            error(f"Video file not found: {video_path}")
            sys.exit(1)

        # Load config
        config = PipelineConfig()
        if args.config:
            # TODO: Load from config file if needed
            pass

        try:
            # 确定 workdir
            workdir = get_workdir(video_path, Path(args.output_dir))
            
            # 创建 manifest
            manifest_path = workdir / "manifest.json"
            manifest = Manifest(manifest_path)
            
            # 设置 job 信息
            job_id = str(uuid.uuid4())
            manifest.set_job(job_id, str(workdir))
            manifest.save()
            
            # 构建 RunContext
            config_dict = config_to_dict(config)
            config_dict["video_path"] = str(video_path.absolute())
            
            ctx = RunContext(
                job_id=job_id,
                workspace=str(workdir),
                config=config_dict,
            )
            
            # 创建 runner
            runner = PhaseRunner(manifest, workdir)
            
            # 运行 pipeline
            outputs = runner.run_pipeline(
                phases=ALL_PHASES,
                ctx=ctx,
                to_phase=args.to,
                from_phase=args.from_phase,
            )
            
            success("Pipeline completed successfully")
            for key, path in outputs.items():
                info(f"{key}: {path}")
                
        except Exception as e:
            error(f"Pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    main()
