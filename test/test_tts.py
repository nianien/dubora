#!/usr/bin/env python3
"""
TTS 测试 CLI — 统一入口，支持多模型切换。

用法:
  # VolcEngine seed-tts
  python test/test_tts.py --model volcengine --text "你好世界"
  python test/test_tts.py --model volcengine --text "Hello" --voice zh_female_shuangkuaisisi_moon_bigtts

  # Gemini TTS
  python test/test_tts.py --model gemini --text "Hello world" --voice Kore

  # Fish Speech (Fish Audio SDK)
  python test/test_tts.py --model fish --text "Hello world"

  # Fish Speech V1.5 (SiliconFlow API)
  python test/test_tts.py --model fish --fish-backend siliconflow --text "Hello world"
  python test/test_tts.py --model fish --fish-backend siliconflow --text "你好" --ref-audio ref.wav --ref-text "参考音频文本"

  # IndexTTS2 (fal.ai 云端, 推荐)
  python test/test_tts.py --model indextts --ref-audio ref.wav --text "你好"
  python test/test_tts.py --model indextts --ref-audio ref.wav --text "你好" --emo-vector "0,0.9,0,0,0,0,0,0"

  # IndexTTS2 (SiliconFlow 云端)
  python test/test_tts.py --model indextts --indextts-backend siliconflow --ref-audio ref.wav --text "你好"

  # 批量合成对话脚本
  python test/test_tts.py --model indextts --script dialogue.json --ref-audio default_voice.wav --output output/
"""

import argparse
import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dubora_core.config.settings import load_env_file

load_env_file(".env.test")

# 各模型默认音色
DEFAULT_VOICES = {
    "volcengine": "zh_female_shuangkuaisisi_moon_bigtts",
    "gemini": "Kore",
    "fish": "",
    "indextts": "",
}


def create_provider(args):
    """根据 --model 参数创建对应的 TTS provider。"""
    if args.model == "volcengine":
        from providers.tts_volcengine import VolcEngineTTSProvider
        return VolcEngineTTSProvider(
            resource_id=args.resource_id,
            sample_rate=args.sample_rate,
        )

    if args.model == "gemini":
        from providers.tts_gemini import GeminiTTSProvider
        return GeminiTTSProvider()

    if args.model == "fish":
        from providers.tts_fish import FishTTSProvider
        return FishTTSProvider(backend=args.fish_backend)

    if args.model == "indextts":
        from providers.tts_indextts import IndexTTSProvider
        return IndexTTSProvider(backend=args.indextts_backend)

    raise ValueError(f"未知模型: {args.model}")


def build_extra_kwargs(args):
    """从 CLI 参数构建额外的 synthesize kwargs。"""
    kwargs = {}
    if args.ref_audio:
        kwargs["ref_audio"] = args.ref_audio
    if args.ref_text:
        kwargs["ref_text"] = args.ref_text
    if args.emo_audio:
        kwargs["emo_audio"] = args.emo_audio
    if args.emo_vector:
        kwargs["emo_vector"] = [float(x) for x in args.emo_vector.split(",")]
    if args.emo_alpha is not None:
        kwargs["emo_alpha"] = args.emo_alpha
    if args.speech_length:
        kwargs["speech_length"] = args.speech_length
    if args.speed:
        kwargs["speed"] = args.speed
    return kwargs


def synthesize_single(provider, text: str, voice: str, out_dir: Path, index: int = 0, **kwargs) -> Path:
    """合成单句并保存。"""
    audio = provider.synthesize(text, voice, **kwargs)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{index:03d}_{provider.name}.wav"
    out_path.write_bytes(audio)
    return out_path


def synthesize_script(provider, script_path: Path, out_dir: Path, default_voice: str, **extra_kwargs):
    """批量合成对话脚本。"""
    with open(script_path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    if not isinstance(entries, list):
        raise ValueError("对话脚本应为 JSON 数组")

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for i, entry in enumerate(entries):
        text = entry.get("text", "")
        voice = entry.get("voice", default_voice)
        speaker = entry.get("speaker", str(i))

        if not text.strip():
            print(f"[WARN] 跳过空文本 (index={i})", file=sys.stderr)
            continue

        # 合并脚本条目参数与全局参数
        kwargs = {**extra_kwargs}
        if entry.get("emotion"):
            kwargs["emotion"] = entry["emotion"]
        if entry.get("ref_audio"):
            kwargs["ref_audio"] = entry["ref_audio"]
        if entry.get("speech_length"):
            kwargs["speech_length"] = entry["speech_length"]

        print(f"[INFO] 合成 [{i}] speaker={speaker} voice={voice}: {text[:40]}...", file=sys.stderr)
        audio = provider.synthesize(text, voice, **kwargs)
        out_path = out_dir / f"{i:03d}_spk{speaker}_{provider.name}.wav"
        out_path.write_bytes(audio)
        paths.append(str(out_path))
        print(f"[INFO] 已保存: {out_path}", file=sys.stderr)

    return paths


def main():
    parser = argparse.ArgumentParser(
        description="TTS 测试 CLI — 统一入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--model", "-m",
        required=True,
        choices=["volcengine", "gemini", "fish", "indextts"],
        help="TTS 模型",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--text", "-t",
        help="单句文本",
    )
    group.add_argument(
        "--script", "-s",
        help="对话脚本 JSON 文件路径",
    )
    parser.add_argument(
        "--output", "-o",
        help="输出目录（默认 test_out/tts/）",
    )
    parser.add_argument(
        "--voice", "-v",
        help="音色 ID（各模型不同）",
    )

    # ── 声音克隆 ────────────────────────────────────────
    parser.add_argument(
        "--ref-audio",
        help="参考音频文件路径（声音克隆，IndexTTS2 / Fish SiliconFlow）",
    )
    parser.add_argument(
        "--ref-text",
        help="参考音频对应文本（Fish SiliconFlow 声音克隆需要）",
    )

    # ── IndexTTS2 专属 ──────────────────────────────────
    parser.add_argument(
        "--indextts-backend",
        choices=["fal", "siliconflow"],
        default="fal",
        help="IndexTTS2 后端: fal (fal.ai, 推荐) / siliconflow（默认 fal）",
    )
    parser.add_argument(
        "--emo-audio",
        help="情绪参考音频（IndexTTS2，可与 --ref-audio 来自不同说话人）",
    )
    parser.add_argument(
        "--emo-vector",
        help="情绪向量，8个浮点数逗号分隔: happy,angry,sad,afraid,disgusted,melancholic,surprised,calm（IndexTTS2）",
    )
    parser.add_argument(
        "--emo-alpha",
        type=float,
        default=None,
        help="情绪强度 0.0-1.0（IndexTTS2，默认 0.8）",
    )
    parser.add_argument(
        "--speech-length",
        type=int,
        help="目标音频时长，毫秒（IndexTTS2）",
    )

    # ── Fish Speech 专属 ────────────────────────────────
    parser.add_argument(
        "--fish-backend",
        choices=["fish-audio", "siliconflow"],
        default="fish-audio",
        help="Fish Speech 后端（默认 fish-audio）",
    )

    # ── 通用 ────────────────────────────────────────────
    parser.add_argument(
        "--speed",
        type=float,
        help="语速倍率（Fish SiliconFlow: 0.25-4.0 / VolcEngine: speed_ratio）",
    )

    # ── VolcEngine 专属 ─────────────────────────────────
    parser.add_argument(
        "--resource-id",
        default="seed-tts-1.0",
        help="VolcEngine resource ID（默认 seed-tts-1.0）",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=24000,
        help="采样率（默认 24000）",
    )

    args = parser.parse_args()

    out_dir = Path(args.output) if args.output else Path("test_out/tts")
    voice = args.voice or DEFAULT_VOICES.get(args.model, "")

    # IndexTTS2: ref-audio 就是 voice (spk_audio_prompt)
    if args.model == "indextts" and args.ref_audio and not voice:
        voice = args.ref_audio

    provider = create_provider(args)
    extra_kwargs = build_extra_kwargs(args)

    print(f"[INFO] 使用模型: {args.model}", file=sys.stderr)

    if args.text:
        out_path = synthesize_single(provider, args.text, voice, out_dir, **extra_kwargs)
        print(f"[INFO] 已保存: {out_path}", file=sys.stderr)
    else:
        script_path = Path(args.script)
        if not script_path.exists():
            print(f"[ERROR] 脚本文件不存在: {args.script}", file=sys.stderr)
            sys.exit(1)
        paths = synthesize_script(provider, script_path, out_dir, voice, **extra_kwargs)
        print(f"[INFO] 共合成 {len(paths)} 个音频文件", file=sys.stderr)


if __name__ == "__main__":
    main()
