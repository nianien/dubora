#!/usr/bin/env python3
"""ASR 测试 CLI — 多模型统一入口，输出原始结果。"""

import argparse
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dubora_core.config.settings import load_env_file
load_env_file(".env.test")

_PROVIDERS = {
    "doubao":     ("providers.asr_doubao",     "DoubaoASRProvider"),
    "gemini":     ("providers.asr_gemini",     "GeminiASRProvider"),
    "openai":     ("providers.asr_openai",     "OpenAIASRProvider"),
    "qwen":       ("providers.asr_qwen",       "QwenASRProvider"),
    "tencent":    ("providers.asr_tencent",    "TencentASRProvider"),
    "paraformer": ("providers.asr_paraformer", "ParaformerASRProvider"),
    "funasr":     ("providers.asr_funasr",     "FunASRProvider"),
    "fish":       ("providers.asr_fish",       "FishASRProvider"),
    "xfyun":      ("providers.asr_xfyun",      "XfyunASRProvider"),
}

_EPILOG = """\
示例:
  %(prog)s -m doubao                                        -i audio.wav
  %(prog)s -m doubao     --doubao-preset asr_vad_spk        -i audio.wav
  %(prog)s -m gemini     --gemini-model gemini-3-flash-preview -i audio.wav
  %(prog)s -m openai     --openai-model gpt-4o-mini-transcribe -i audio.wav
  %(prog)s -m qwen       --qwen-model qwen3-asr-flash       -i audio.wav
  %(prog)s -m tencent                                        -i audio.wav
  %(prog)s -m paraformer                                     -i audio.wav
  %(prog)s -m funasr     --funasr-device cuda:0              -i audio.wav
  %(prog)s -m fish                                           -i audio.wav
  %(prog)s -m xfyun                                          -i audio.wav

环境变量:
  doubao      DOUBAO_APPID, DOUBAO_ACCESS_TOKEN
  gemini      GEMINI_API_KEY
  openai      OPENAI_API_KEY
  qwen        DASHSCOPE_API_KEY
  tencent     TENCENT_SECRET_ID, TENCENT_SECRET_KEY
  paraformer  DASHSCOPE_API_KEY
  fish        FISH_API_KEY
  xfyun       XFYUN_APPID, XFYUN_SECRET_KEY
"""


def create_provider(args):
    import importlib
    mod_path, cls_name = _PROVIDERS[args.model]
    cls = getattr(importlib.import_module(mod_path), cls_name)

    if args.model == "xfyun":
        return cls(speaker_number=args.xfyun_speakers)
    if args.model == "funasr":
        return cls(model_name=args.funasr_model, device=args.funasr_device)
    if args.model == "gemini":
        return cls(model_name=args.gemini_model)
    if args.model == "openai":
        return cls(model_name=args.openai_model)
    if args.model == "qwen":
        return cls(model_name=args.qwen_model)
    return cls()


def resolve_audio(args, provider):
    """根据 provider.input_type 准备音频输入。"""
    if args.input.startswith(("http://", "https://")):
        return args.input

    path = Path(args.input).resolve()
    if not path.exists():
        raise FileNotFoundError(f"音频文件不存在: {path}")

    input_type = getattr(provider, "input_type", "url")
    if input_type == "file":
        return str(path)

    if args.key:
        blob_key = args.key
    elif "data/pipeline/" in str(path):
        blob_key = f"dramas/{path.parent.parent.name}/asr/{path.name}"
    else:
        blob_key = f"test-asr/{path.name}"

    if input_type == "gcs":
        from dubora_core.utils.file_store import get_gcs_store
        store, label = get_gcs_store(), "GCS"
    else:
        from dubora_core.utils.file_store import get_tos_store
        store, label = get_tos_store(), "TOS"

    store.write_file(path, blob_key)
    url = store.get_url(blob_key, expires=36000)
    print(f"[INFO] 上传 {label}: {blob_key}", file=sys.stderr)
    return url


def main():
    parser = argparse.ArgumentParser(
        description="ASR 测试 CLI — 多模型统一入口",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-m", "--model", required=True, choices=list(_PROVIDERS),
                        help="ASR 模型")
    parser.add_argument("-i", "--input", required=True,
                        help="音频文件路径或 URL")
    parser.add_argument("-o", "--output",
                        help="输出 JSON 路径 (默认 test_out/asr/{stem}_{model}.json)")
    parser.add_argument("--key",
                        help="对象存储 blob key (默认自动推导)")

    doubao = parser.add_argument_group("doubao")
    doubao.add_argument("--doubao-preset", default="asr_spk_semantic",
                        choices=["asr_vad_spk", "asr_vad_spk_smooth", "asr_spk_semantic"],
                        help="预设 (默认 asr_spk_semantic)")
    doubao.add_argument("--doubao-hotwords", nargs="*",
                        help="热词列表")

    gemini = parser.add_argument_group("gemini")
    gemini.add_argument("--gemini-model", default="gemini-3.1-pro-preview",
                        choices=["gemini-3.1-pro-preview", "gemini-3-flash-preview"],
                        help="模型 (默认 gemini-3.1-pro-preview)")

    openai = parser.add_argument_group("openai")
    openai.add_argument("--openai-model", default="gpt-4o-transcribe-diarize",
                        choices=["gpt-4o-transcribe-diarize", "gpt-4o-transcribe",
                                 "gpt-4o-mini-transcribe", "whisper-1"],
                        help="模型 (默认 gpt-4o-transcribe-diarize)")

    qwen = parser.add_argument_group("qwen")
    qwen.add_argument("--qwen-model", default="qwen3-asr-flash-filetrans",
                      choices=["qwen3-asr-flash-filetrans", "qwen3-asr-flash"],
                      help="模型 (默认 qwen3-asr-flash-filetrans)")

    xfyun = parser.add_argument_group("xfyun")
    xfyun.add_argument("--xfyun-speakers", type=int, default=0,
                        help="发音人数 (0=盲分, 默认 0)")

    funasr = parser.add_argument_group("funasr")
    funasr.add_argument("--funasr-model", default="paraformer-zh",
                        choices=["paraformer-zh", "paraformer-en", "paraformer-zh-streaming"],
                        help="模型 (默认 paraformer-zh)")
    funasr.add_argument("--funasr-device", default="cpu",
                        choices=["cpu", "cuda", "cuda:0", "cuda:1"],
                        help="设备 (默认 cpu)")

    args = parser.parse_args()

    provider = create_provider(args)
    audio_input = resolve_audio(args, provider)
    print(f"[INFO] {args.model}: {args.input}", file=sys.stderr)

    kwargs = {}
    if args.model == "doubao":
        kwargs = {"preset": args.doubao_preset}
        if args.doubao_hotwords:
            kwargs["hotwords"] = args.doubao_hotwords
    elif args.model == "xfyun" and not args.input.startswith("http"):
        import wave
        with wave.open(args.input, 'rb') as wf:
            kwargs["duration_ms"] = int(round(wf.getnframes() / wf.getframerate() * 1000))

    result = provider.transcribe(audio_input, **kwargs)
    json_str = json.dumps(result, ensure_ascii=False, indent=2)

    stem = Path(args.input).stem if not args.input.startswith("http") else "url_input"
    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = Path("test_out/asr")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{stem}_{args.model}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json_str, encoding="utf-8")
    print(f"[INFO] 保存: {out_path}", file=sys.stderr)
    print(json_str)


if __name__ == "__main__":
    main()
