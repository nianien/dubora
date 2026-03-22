#!/usr/bin/env python3
"""
声音复刻测试 CLI — VolcEngine V3 音色训练 + 查询 + TTS 合成。

典型工作流: ASR 识别音轴 → cut 剪切片段 → train 训练音色 → status 查询状态 → synthesize 合成语音

命令:

  cut        按时间轴剪切音频片段（用于从多人对话中截取单人语音）
  train      上传参考音频，训练复刻音色
  status     查询音色训练状态
  list       列出所有自建复刻音色（通过 Volcengine OpenAPI）
  synthesize 用已训练的复刻音色合成语音

示例:

  # 1. 先用 test_asr.py 识别音轴，拿到各说话人的 start/end 时间
  python test/test_asr.py -m doubao -i dialogue.wav

  # 2. 根据音轴剪切目标说话人的片段
  python test/test_voice_clone.py cut -i dialogue.wav --start 00:04.600 --end 00:08.500
  python test/test_voice_clone.py cut -i dialogue.wav --start 00:04.600 --end 00:08.500 -o clip.wav

  # 3. 用剪切的片段训练音色
  python test/test_voice_clone.py train --speaker-id S_xxx --audio clip.wav
  python test/test_voice_clone.py train --speaker-id S_xxx --audio clip.wav --language en --model-types 1 4

  # 4. 查询训练状态（status=2 Success 或 4 Active 时可用）
  python test/test_voice_clone.py status --speaker-id S_xxx

  # 5. 用复刻音色合成语音
  python test/test_voice_clone.py synthesize --speaker-id S_xxx --text "你好世界"
  python test/test_voice_clone.py synthesize --speaker-id S_xxx --text "Hello" --resource-id seed-icl-2.0

环境变量:
  DOUBAO_APPID          火山引擎 APP ID
  DOUBAO_ACCESS_TOKEN   火山引擎 Access Token
"""

import argparse
import base64
import datetime
import hashlib
import hmac
import json
import struct
import subprocess
import sys
import uuid
from pathlib import Path

import requests

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dubora_core.config.settings import load_env_file

load_env_file(".env.test")

# ── Constants ────────────────────────────────────────────────────────────────

VOICE_CLONE_URL = "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
GET_VOICE_URL = "https://openspeech.bytedance.com/api/v3/tts/get_voice"

LANGUAGE_MAP = {
    "cn": 0, "zh": 0,
    "en": 1,
    "ja": 2,
    "es": 3,
    "id": 4,
    "pt": 5,
    "de": 6,
    "fr": 7,
}

STATUS_MAP = {
    0: "NotFound",
    1: "Training",
    2: "Success",
    3: "Failed",
    4: "Active",
}

MODEL_TYPE_MAP = {
    1: "ICL 1.0",
    2: "DiT Standard (音色, 不还原风格)",
    3: "DiT Restore (音色+口音+语速)",
    4: "ICL 2.0",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def get_credentials():
    """从环境变量获取 DOUBAO_APPID / DOUBAO_ACCESS_TOKEN。"""
    import os
    app_id = os.getenv("DOUBAO_APPID")
    access_key = os.getenv("DOUBAO_ACCESS_TOKEN")
    if not app_id or not access_key:
        print("[ERROR] 需要设置 DOUBAO_APPID 和 DOUBAO_ACCESS_TOKEN 环境变量", file=sys.stderr)
        sys.exit(1)
    return app_id, access_key


def make_headers(app_id: str, access_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Api-App-Key": app_id,
        "X-Api-Access-Key": access_key,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }


def print_voice_status(data: dict):
    """格式化打印音色状态。"""
    print(f"  speaker_id: {data.get('speaker_id')}")
    status = data.get("status", -1)
    print(f"  status: {status} ({STATUS_MAP.get(status, 'Unknown')})")
    lang = data.get("language", 0)
    lang_name = [k for k, v in LANGUAGE_MAP.items() if v == lang and len(k) == 2]
    print(f"  language: {lang} ({lang_name[0] if lang_name else 'unknown'})")
    print(f"  available_training_times: {data.get('available_training_times', 'N/A')}")
    create_time = data.get("create_time")
    if create_time:
        from datetime import datetime
        ts = create_time / 1000 if create_time > 1e12 else create_time
        print(f"  create_time: {datetime.fromtimestamp(ts).isoformat()}")

    speaker_status = data.get("speaker_status", [])
    if speaker_status:
        print(f"  models ({len(speaker_status)}):")
        for ss in speaker_status:
            mt = ss.get("model_type", "?")
            mt_name = MODEL_TYPE_MAP.get(mt, "Unknown")
            demo = ss.get("demo_audio", "")
            demo_short = (demo[:60] + "...") if len(demo) > 60 else demo
            print(f"    model_type={mt} ({mt_name})")
            if demo:
                print(f"      demo_audio: {demo_short}")


def pcm_to_wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    data_size = len(pcm)
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, bits,
        b"data", data_size,
    )
    return header + pcm


# ── Commands ─────────────────────────────────────────────────────────────────


def parse_time(t: str) -> float:
    """解析时间字符串为秒数。支持 MM:SS.mmm / HH:MM:SS.mmm / 纯秒数。"""
    parts = t.split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"无法解析时间: {t}")


def cmd_cut(args):
    """按时间轴剪切音频片段。"""
    audio_path = Path(args.input)
    if not audio_path.exists():
        print(f"[ERROR] 音频文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    start_s = parse_time(args.start)
    end_s = parse_time(args.end)
    duration_s = end_s - start_s
    if duration_s <= 0:
        print(f"[ERROR] end ({args.end}) 必须大于 start ({args.start})", file=sys.stderr)
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = Path("test_out/voice_clone")
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = f"{args.start}_{args.end}".replace(":", "").replace(".", "")
        out_path = out_dir / f"{audio_path.stem}_{tag}.wav"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-ss", f"{start_s:.3f}",
        "-t", f"{duration_s:.3f}",
        "-ar", "24000", "-ac", "1",
        str(out_path),
    ]

    print(f"[INFO] 剪切: {args.start} ~ {args.end} ({duration_s:.1f}s)")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] ffmpeg 失败:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    size = out_path.stat().st_size
    print(f"[INFO] 已保存: {out_path} ({size} bytes)")


def cmd_train(args):
    """训练复刻音色。"""
    app_id, access_key = get_credentials()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"[ERROR] 音频文件不存在: {args.audio}", file=sys.stderr)
        sys.exit(1)

    audio_bytes = audio_path.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    print(f"[INFO] 音频文件: {audio_path} ({len(audio_bytes)} bytes)")
    print(f"[INFO] speaker_id: {args.speaker_id}")

    body = {
        "speaker_id": args.speaker_id,
        "audio": {
            "data": audio_b64,
        },
    }

    # 音频格式
    suffix = audio_path.suffix.lower().lstrip(".")
    if suffix:
        body["audio"]["format"] = suffix

    # 语种
    lang_code = LANGUAGE_MAP.get(args.language, 0)
    body["language"] = lang_code
    print(f"[INFO] language: {args.language} ({lang_code})")

    # model_types
    if args.model_types:
        body["model_types"] = args.model_types
        mt_names = [f"{mt}({MODEL_TYPE_MAP.get(mt, '?')})" for mt in args.model_types]
        print(f"[INFO] model_types: {', '.join(mt_names)}")

    # extra_params
    extra = {}
    if args.text:
        body["audio"]["text"] = args.text
    if args.demo_text:
        extra["demo_text"] = args.demo_text
    if args.denoise is not None:
        extra["enable_audio_denoise"] = args.denoise
    if args.mss:
        extra["voice_clone_enable_mss"] = True
    if args.crop_by_asr:
        extra["enable_crop_by_asr"] = True
    if extra:
        body["extra_params"] = extra

    headers = make_headers(app_id, access_key)
    print(f"[INFO] 发送训练请求...")

    resp = requests.post(VOICE_CLONE_URL, headers=headers, json=body, timeout=120)
    logid = resp.headers.get("X-Tt-Logid", "N/A")
    print(f"[INFO] logid: {logid}")

    if resp.status_code != 200:
        print(f"[ERROR] HTTP {resp.status_code}", file=sys.stderr)
        try:
            err = resp.json()
            print(f"[ERROR] code={err.get('code')}, message={err.get('message')}", file=sys.stderr)
        except Exception:
            print(f"[ERROR] {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    print("[INFO] 训练请求成功:")
    print_voice_status(data)

    # 保存完整响应
    out_dir = Path("test_out/voice_clone")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"train_{args.speaker_id}.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] 完整响应已保存: {out_path}")


def cmd_status(args):
    """查询音色训练状态。"""
    app_id, access_key = get_credentials()
    headers = make_headers(app_id, access_key)
    body = {"speaker_id": args.speaker_id}

    print(f"[INFO] 查询 speaker_id: {args.speaker_id}")

    resp = requests.post(GET_VOICE_URL, headers=headers, json=body, timeout=30)
    logid = resp.headers.get("X-Tt-Logid", "N/A")
    print(f"[INFO] logid: {logid}")

    if resp.status_code != 200:
        print(f"[ERROR] HTTP {resp.status_code}", file=sys.stderr)
        try:
            err = resp.json()
            print(f"[ERROR] code={err.get('code')}, message={err.get('message')}", file=sys.stderr)
        except Exception:
            print(f"[ERROR] {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    print("[INFO] 音色状态:")
    print_voice_status(data)

    # 输出完整 JSON
    print(json.dumps(data, ensure_ascii=False, indent=2))


def volc_openapi_sign(ak: str, sk: str, service: str, region: str, action: str,
                      version: str, body: bytes) -> dict:
    """Volcengine OpenAPI V4 HMAC-SHA256 签名，返回带 Authorization 的请求头。"""
    now = datetime.datetime.utcnow()
    date_str = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")

    host = "open.volcengineapi.com"
    content_type = "application/json"
    body_hash = hashlib.sha256(body).hexdigest()

    # 1. Canonical Request
    canonical_qs = f"Action={action}&Version={version}"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-date:{date_str}\n"
    )
    signed_headers = "content-type;host;x-date"
    canonical_request = "\n".join([
        "POST",
        "/",
        canonical_qs,
        canonical_headers,
        signed_headers,
        body_hash,
    ])

    # 2. String to Sign
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256",
        date_str,
        credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])

    # 3. Signing Key
    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _hmac(sk.encode(), short_date)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    signing_key = _hmac(k_service, "request")

    # 4. Signature
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    return {
        "Content-Type": content_type,
        "Host": host,
        "X-Date": date_str,
        "Authorization": authorization,
    }


def cmd_list(args):
    """列出所有自建复刻音色（通过 Volcengine OpenAPI）。"""
    import os

    ak = os.getenv("VOLC_ACCESS_KEY")
    sk = os.getenv("VOLC_SECRET_KEY")
    app_id = os.getenv("DOUBAO_APPID")
    if not ak or not sk:
        print("[ERROR] 需要设置 VOLC_ACCESS_KEY 和 VOLC_SECRET_KEY 环境变量", file=sys.stderr)
        sys.exit(1)
    if not app_id:
        print("[ERROR] 需要设置 DOUBAO_APPID 环境变量", file=sys.stderr)
        sys.exit(1)

    body_dict = {
        "AppID": app_id,
        "ResourceIDs": [
            "volc.megatts.voiceclone",
            "volc.seedicl.voiceclone",
            "volc.dialog.voiceclone",
        ],
    }
    if args.speaker_ids:
        body_dict["SpeakerIDs"] = args.speaker_ids

    body = json.dumps(body_dict).encode()

    headers = volc_openapi_sign(
        ak=ak, sk=sk,
        service="speech_saas_prod",
        region="cn-north-1",
        action="ListMegaTTSTrainStatus",
        version="2025-05-21",
        body=body,
    )

    url = "https://open.volcengineapi.com/?Action=ListMegaTTSTrainStatus&Version=2025-05-21"
    print(f"[INFO] 查询 AppID={app_id} 下的所有复刻音色...")

    resp = requests.post(url, headers=headers, data=body, timeout=30)

    if resp.status_code != 200:
        print(f"[ERROR] HTTP {resp.status_code}", file=sys.stderr)
        print(f"[ERROR] {resp.text[:1000]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()

    # 检查 OpenAPI 错误
    resp_meta = data.get("ResponseMetadata", {})
    error = resp_meta.get("Error")
    if error:
        print(f"[ERROR] {error.get('Code')}: {error.get('Message')}", file=sys.stderr)
        sys.exit(1)

    result = data.get("Result", {})
    speakers = result.get("SpeakerList", [])
    print(f"[INFO] 共找到 {len(speakers)} 个复刻音色:\n")

    for sp in speakers:
        sid = sp.get("SpeakerID", "?")
        status = sp.get("State", -1)
        status_name = STATUS_MAP.get(status, f"Unknown({status})")
        create_time = sp.get("CreateTime", "")
        print(f"  {sid}  status={status}({status_name})  created={create_time}")

        # 显示支持的模型
        resources = sp.get("ResourceList", [])
        for res in resources:
            rid = res.get("ResourceID", "?")
            rstate = res.get("State", -1)
            rstate_name = STATUS_MAP.get(rstate, f"Unknown({rstate})")
            print(f"    {rid}: {rstate}({rstate_name})")
        print()

    # 保存完整响应
    out_dir = Path("test_out/voice_clone")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "list_voices.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] 完整响应已保存: {out_path}")


def cmd_batch_status(args):
    """批量查询多个 speaker_id 的状态（通过 V3 get_voice 接口）。"""
    app_id, access_key = get_credentials()
    headers = make_headers(app_id, access_key)

    speaker_ids = args.speaker_ids
    print(f"[INFO] 批量查询 {len(speaker_ids)} 个 speaker_id...\n")

    results = []
    for sid in speaker_ids:
        body = {"speaker_id": sid}
        try:
            resp = requests.post(GET_VOICE_URL, headers=headers, json=body, timeout=15)
            if resp.status_code != 200:
                print(f"  {sid}: HTTP {resp.status_code}")
                continue
            data = resp.json()
            status = data.get("status", -1)
            status_name = STATUS_MAP.get(status, f"Unknown({status})")

            speaker_status = data.get("speaker_status", [])
            models = [MODEL_TYPE_MAP.get(ss.get("model_type"), "?") for ss in speaker_status]
            models_str = ", ".join(models) if models else "N/A"

            create_time = data.get("create_time")
            time_str = ""
            if create_time:
                from datetime import datetime as dt
                ts = create_time / 1000 if create_time > 1e12 else create_time
                time_str = dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

            print(f"  {sid}  {status}({status_name})  models=[{models_str}]  created={time_str}")
            results.append(data)
        except Exception as e:
            print(f"  {sid}: {e}")

    # 保存完整响应
    if results:
        out_dir = Path("test_out/voice_clone")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "batch_status.json"
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[INFO] 完整响应已保存: {out_path}")


def cmd_synthesize(args):
    """用复刻音色合成语音（调用已有 TTS client）。"""
    app_id, access_key = get_credentials()

    resource_id = args.resource_id
    print(f"[INFO] speaker_id: {args.speaker_id}")
    print(f"[INFO] resource_id: {resource_id}")
    print(f"[INFO] text: {args.text}")

    from dubora_core.infra.tts_client import call_volcengine_tts

    pcm_data, sentence_data = call_volcengine_tts(
        text=args.text,
        speaker=args.speaker_id,
        app_id=app_id,
        access_key=access_key,
        resource_id=resource_id,
        sample_rate=args.sample_rate,
    )

    wav_data = pcm_to_wav(pcm_data, sample_rate=args.sample_rate)

    out_dir = Path(args.output) if args.output else Path("test_out/voice_clone")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"synth_{args.speaker_id}_{resource_id}.wav"
    out_path.write_bytes(wav_data)
    print(f"[INFO] 音频已保存: {out_path} ({len(wav_data)} bytes)")

    if sentence_data:
        meta_path = out_path.with_suffix(".json")
        meta_path.write_text(json.dumps(sentence_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] Sentence 数据已保存: {meta_path}")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── cut ──
    p_cut = sub.add_parser(
        "cut", help="按时间轴剪切音频片段",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="从音频文件中按起止时间截取片段，输出 24kHz 单声道 WAV。\n"
                    "配合 test_asr.py 识别的音轴使用，可从多人对话中提取单人语音用于复刻训练。",
        epilog="示例:\n"
               "  python test/test_voice_clone.py cut -i dialogue.wav --start 00:04.600 --end 00:08.500\n"
               "  python test/test_voice_clone.py cut -i dialogue.wav --start 1:23.0 --end 1:35.500 -o clip.wav\n"
               "  python test/test_voice_clone.py cut -i dialogue.wav --start 4.6 --end 8.5\n",
    )
    p_cut.add_argument("--input", "-i", required=True,
                       help="输入音频文件路径 (支持 wav/mp3/m4a 等 FFmpeg 可解码格式)")
    p_cut.add_argument("--start", required=True,
                       help="起始时间，支持格式: MM:SS.mmm (如 00:04.600) / HH:MM:SS.mmm / 纯秒数 (如 4.6)")
    p_cut.add_argument("--end", required=True,
                       help="结束时间，格式同 --start")
    p_cut.add_argument("--output", "-o",
                       help="输出 WAV 文件路径 (默认 test_out/voice_clone/{stem}_{start}_{end}.wav)")

    # ── train ──
    p_train = sub.add_parser(
        "train", help="上传参考音频，训练复刻音色",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="上传一段参考音频到 VolcEngine V3 接口，训练自定义复刻音色。\n"
                    "音频要求: wav/mp3/ogg/m4a/aac/pcm 格式，最大 10MB，建议清晰人声、低噪声。",
        epilog="示例:\n"
               "  python test/test_voice_clone.py train --speaker-id S_xxx --audio ref.wav\n"
               "  python test/test_voice_clone.py train --speaker-id S_xxx --audio ref.wav --language en\n"
               "  python test/test_voice_clone.py train --speaker-id S_xxx --audio ref.wav --model-types 1 4 --mss\n",
    )
    p_train.add_argument("--speaker-id", required=True,
                         help="音色唯一 ID，需从火山引擎控制台预生成 (如 S_xxxxxxx)")
    p_train.add_argument("--audio", required=True,
                         help="参考音频文件路径 (wav/mp3/ogg/m4a/aac/pcm，最大 10MB)")
    p_train.add_argument("--language", default="cn",
                         choices=list(LANGUAGE_MAP.keys()),
                         help="音频语种 (默认 cn)。cn/en 支持所有模型; ja/es/id/pt 仅支持 ICL 1.0; de/fr 仅 DiT 标准版")
    p_train.add_argument("--model-types", type=int, nargs="*", metavar="TYPE",
                         help="训练模型类型列表 (可多选): 1=ICL 1.0, 2=DiT 标准版(只还原音色), "
                              "3=DiT 还原版(音色+口音+语速), 4=ICL 2.0。不传则服务端自动选择")
    p_train.add_argument("--text",
                         help="参考文本，服务端会对比音频与文本的差异，差异过大会拒绝 (错误码 45001109)")
    p_train.add_argument("--demo-text",
                         help="试听文本 (4-80 字)，训练成功后生成试听音频，需与 --language 语种匹配")
    p_train.add_argument("--denoise", type=bool, default=None,
                         help="是否开启降噪 (ICL 1.0 默认开启，ICL 2.0 默认关闭)。音频噪声大时建议开启")
    p_train.add_argument("--mss", action="store_true",
                         help="开启音源分离，去除音频中的背景音乐")
    p_train.add_argument("--crop-by-asr", action="store_true",
                         help="开启 ASR 截断，精准定位字音位置避免发音被切开")

    # ── status ──
    p_status = sub.add_parser(
        "status", help="查询音色训练状态",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="查询已提交的复刻音色训练状态。\n"
                    "状态: 0=NotFound, 1=Training, 2=Success, 3=Failed, 4=Active\n"
                    "状态为 2(Success) 或 4(Active) 时可调用 synthesize 合成语音。",
        epilog="示例:\n"
               "  python test/test_voice_clone.py status --speaker-id S_xxx\n",
    )
    p_status.add_argument("--speaker-id", required=True,
                          help="音色唯一 ID (与 train 时传入的一致)")

    # ── synthesize ──
    p_synth = sub.add_parser(
        "synthesize", help="用复刻音色合成语音",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="使用已训练成功的复刻音色合成语音，输出 WAV 文件。\n"
                    "通过 --resource-id 选择不同的复刻版本效果。",
        epilog="示例:\n"
               "  python test/test_voice_clone.py synthesize --speaker-id S_xxx --text '你好世界'\n"
               "  python test/test_voice_clone.py synthesize --speaker-id S_xxx --text 'Hello' --resource-id seed-icl-2.0\n"
               "  python test/test_voice_clone.py synthesize --speaker-id S_xxx --text '你好' -o output/\n",
    )
    p_synth.add_argument("--speaker-id", required=True,
                         help="音色唯一 ID (需已训练成功，status 为 2 或 4)")
    p_synth.add_argument("--text", required=True,
                         help="要合成的文本内容")
    p_synth.add_argument("--resource-id", default="seed-icl-1.0",
                         choices=["seed-icl-1.0", "seed-icl-1.0-concurr", "seed-icl-2.0"],
                         help="合成引擎 (默认 seed-icl-1.0): "
                              "seed-icl-1.0=ICL1.0字符版, seed-icl-1.0-concurr=ICL1.0并发版, seed-icl-2.0=ICL2.0字符版")
    p_synth.add_argument("--sample-rate", type=int, default=24000,
                         help="输出采样率 (默认 24000)")
    p_synth.add_argument("--output", "-o",
                         help="输出目录 (默认 test_out/voice_clone/)")

    # ── batch-status ──
    p_batch = sub.add_parser(
        "batch-status", help="批量查询多个 speaker_id 状态",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="通过 V3 get_voice 接口逐个查询多个 speaker_id 的训练状态。\n"
                    "不需要 OpenAPI IAM 权限，使用 DOUBAO_APPID / DOUBAO_ACCESS_TOKEN 认证。",
        epilog="示例:\n"
               "  python test/test_voice_clone.py batch-status --speaker-ids S_xxx S_yyy S_zzz\n",
    )
    p_batch.add_argument("--speaker-ids", nargs="+", required=True, metavar="SID",
                         help="要查询的 speaker_id 列表")

    # ── list ──
    p_list = sub.add_parser(
        "list", help="列出所有自建复刻音色",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="通过 Volcengine OpenAPI 列出当前 AppID 下所有复刻音色及状态。\n"
                    "需要 VOLC_ACCESS_KEY / VOLC_SECRET_KEY 环境变量 (AK/SK 签名认证)。",
        epilog="示例:\n"
               "  python test/test_voice_clone.py list\n"
               "  python test/test_voice_clone.py list --speaker-ids S_xxx S_yyy\n",
    )
    p_list.add_argument("--speaker-ids", nargs="*", metavar="SID",
                        help="只查询指定的 speaker_id 列表 (不传则查询全部)")

    args = parser.parse_args()

    {
        "cut": cmd_cut,
        "train": cmd_train,
        "status": cmd_status,
        "synthesize": cmd_synthesize,
        "batch-status": cmd_batch_status,
        "list": cmd_list,
    }[args.command](args)


if __name__ == "__main__":
    main()
