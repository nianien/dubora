#!/usr/bin/env python3
"""
简化的 ASR 测试工具：本地文件 → TOS 上传 → ASR 调用 → 返回结果

功能：
1. 接受本地音频文件路径
2. 自动上传到 TOS 获取 URL
3. 调用 ASR 模型
4. 返回 ASR 结果（原始响应和 utterances）

使用方法:
    python test/test_asr_local.py <音频文件路径> [--preset <预设名>] [--output <输出目录>]

示例:
    python test/test_asr_local.py audio/1.wav
    python test/test_asr_local.py audio/1.wav --preset asr_vad_spk
    python test/test_asr_local.py audio/1.wav --preset asr_vad_spk --output results/
"""
import argparse
import json
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from dubora import load_env_file
from dubora.pipeline.processors.asr import transcribe
from dubora.infra.storage.tos import TosStorage
from dubora.utils.logger import info, success, error


def main():
    parser = argparse.ArgumentParser(
        description="ASR 测试工具：本地文件 → TOS 上传 → ASR 调用 → 返回结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:

1. 基本用法（使用默认预设）:
   python test/test_asr_local.py audio/1.wav

2. 指定预设:
   python test/test_asr_local.py audio/1.wav --preset asr_vad_spk

3. 指定多个预设（逗号分隔）:
   python test/test_asr_local.py audio/1.wav --preset asr_vad_spk,asr_vad_spk_smooth

4. 执行所有预设（不传 --preset）:
   python test/test_asr_local.py audio/1.wav

5. 指定输出目录（默认: test_output/asr）:
   python test/test_asr_local.py audio/1.wav --preset asr_vad_spk --output results/

6. 指定 TOS 上传 prefix:
   python test/test_asr_local.py audio/1.wav --prefix dbqsfy

环境变量:
  DOUBAO_APPID: 应用标识（必填）
  DOUBAO_ACCESS_TOKEN: 访问令牌（必填）
  TOS_ACCESS_KEY_ID: TOS 访问密钥（必填，用于上传）
  TOS_SECRET_ACCESS_KEY: TOS 密钥（必填，用于上传）
        """
    )

    parser.add_argument(
        "file",
        type=str,
        help="本地音频文件路径"
    )

    parser.add_argument(
        "--preset",
        type=str,
        default=None,
        help="ASR 预设名称，支持逗号分隔的多个预设（例如: asr_vad_spk 或 asr_vad_spk,asr_vad_spk_smooth）。不传则执行所有预设"
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出目录（默认: test_output/asr）"
    )

    parser.add_argument(
        "--hotwords",
        type=str,
        nargs="+",
        default=None,
        help="热词列表（例如: --hotwords 平安 平安哥 哥）"
    )

    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="TOS 上传时的目录前缀（例如: --prefix dbqsfy）。如果不指定，会从文件路径中提取"
    )

    args = parser.parse_args()

    # 加载环境变量
    load_env_file()

    # 检查文件是否存在
    audio_path = Path(args.file)
    if not audio_path.exists():
        error(f"文件不存在: {audio_path}")
        sys.exit(1)

    if not audio_path.is_file():
        error(f"不是文件: {audio_path}")
        sys.exit(1)

    info(f"输入文件: {audio_path}")
    info(f"文件大小: {audio_path.stat().st_size / 1024 / 1024:.2f} MB")

    # 解析预设列表（支持逗号分隔，不传则使用所有预设）
    if args.preset:
        presets = [p.strip() for p in args.preset.split(",") if p.strip()]
        if not presets:
            error("--preset 参数不能为空")
            sys.exit(1)
    else:
        # 获取所有可用预设
        from dubora.models.doubao import get_presets
        presets = sorted(get_presets().keys())
        info(f"未指定预设，将执行所有预设: {', '.join(presets)}")

    # 1. 上传到 TOS 获取 URL
    info("上传文件到 TOS...")
    try:
        storage = TosStorage()
        
        # 确定 prefix：优先使用命令行参数，否则从文件路径提取
        prefix = args.prefix
        if prefix is None:
            # 从文件路径提取系列名（如果有）
            # 例如: videos/dbqsfy/1.wav -> dbqsfy
            if len(audio_path.parts) >= 2:
                parts = audio_path.parts
                if "videos" in parts:
                    idx = parts.index("videos")
                    if idx + 1 < len(parts):
                        prefix = parts[idx + 1]
        
        if prefix:
            info(f"使用 prefix: {prefix}")
        
        audio_url = storage.upload(audio_path, prefix=prefix)
        success(f"上传成功: {audio_url}")
    except Exception as e:
        error(f"上传失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 2. 调用 ASR（支持多个预设）
    if args.output:
        output_dir = Path(args.output)
    else:
        # 默认保存到 test_output/asr 目录
        output_dir = Path("test_output/asr")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    failed = []
    
    for preset in presets:
        info(f"\n处理预设: {preset}")
        try:
            raw_response, utterances = transcribe(
                audio_url=audio_url,
                preset=preset,
                hotwords=args.hotwords,
            )
            
            if not utterances:
                error(f"{preset}: ASR 返回空结果")
                failed.append(preset)
                continue
            
            success(f"{preset}: ASR 成功，{len(utterances)} 个片段")
            
            # 保存结果（每个预设单独保存）
            raw_response_path = output_dir / f"{audio_path.stem}-{preset}-asr-raw-response.json"
            with open(raw_response_path, "w", encoding="utf-8") as f:
                json.dump(raw_response, f, indent=2, ensure_ascii=False)
            info(f"  原始响应: {raw_response_path}")
            
            utterances_path = output_dir / f"{audio_path.stem}-{preset}-utterances.json"
            utterances_data = [
                {
                    "start_ms": u.start_ms,
                    "end_ms": u.end_ms,
                    "start": u.start_ms / 1000.0,  # 转换为秒
                    "end": u.end_ms / 1000.0,  # 转换为秒
                    "text": u.text,
                    "speaker": u.speaker,
                }
                for u in utterances
            ]
            with open(utterances_path, "w", encoding="utf-8") as f:
                json.dump(utterances_data, f, indent=2, ensure_ascii=False)
            info(f"  Utterances: {utterances_path}")
            
            results.append({
                "preset": preset,
                "utterances_count": len(utterances),
                "raw_response_path": raw_response_path,
                "utterances_path": utterances_path,
            })
            
        except Exception as e:
            error(f"{preset}: ASR 失败: {e}")
            failed.append(preset)
            import traceback
            traceback.print_exc()
    
    # 3. 打印摘要
    print()
    success("=" * 60)
    success("ASR 结果摘要")
    success("=" * 60)
    info(f"音频 URL: {audio_url}")
    info(f"处理预设数量: {len(presets)}")
    info(f"成功: {len(results)}, 失败: {len(failed)}")
    
    if results:
        print()
        info("成功的结果:")
        for r in results:
            print(f"  ✓ {r['preset']}: {r['utterances_count']} 个片段")
            print(f"    原始响应: {r['raw_response_path']}")
            print(f"    Utterances: {r['utterances_path']}")
    
    if failed:
        print()
        error("失败的预设:")
        for preset in failed:
            print(f"  ✗ {preset}")
    
    # 打印第一个成功结果的前几个片段（作为示例）
    if results:
        first_result = results[0]
        preset = first_result['preset']
        utterances_path = first_result['utterances_path']
        
        with open(utterances_path, "r", encoding="utf-8") as f:
            utterances_data = json.load(f)
        
        if utterances_data:
            print()
            info(f"示例片段（来自 {preset}，前 5 个）:")
            for i, u in enumerate(utterances_data[:5], 1):
                speaker_info = f" [Speaker: {u['speaker']}]" if u.get('speaker') else ""
                print(f"  {i}. [{u['start']:.2f}s - {u['end']:.2f}s]{speaker_info} {u['text']}")
    
    success("=" * 60)
    
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
