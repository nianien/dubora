#!/usr/bin/env python3
"""
豆包音视频翻译（TranslateAudio）测试脚本

用途：
- 给一个 TOS 视频/音频地址（Uri），调用火山引擎机器翻译「音视频文件翻译 API」
- 输出英文字幕 SRT 文件，方便你直接肉眼评估 Doubao / Volcengine MT 效果

参考文档：
- 音视频文件翻译 API（SubmitAudio / QueryAudio）：
  https://www.volcengine.com/docs/4640/78985

依赖：
- 需要安装官方 Python SDK（示例）：
  pip install volcengine

环境变量（自动从项目根目录的 .env 文件加载）：
- VOLC_ACCESS_KEY        火山引擎 AccessKey（必填）
- VOLC_SECRET_KEY        火山引擎 SecretKey（必填）
- TOS_ACCESS_KEY_ID      TOS AccessKey（必填，如果使用 object key）
- TOS_SECRET_ACCESS_KEY  TOS SecretKey（必填，如果使用 object key）
- TOS_BUCKET             TOS 桶名（默认：pikppo-video）
- TOS_REGION             TOS 区域（默认：cn-beijing）
- TOS_ENDPOINT           TOS 端点（默认：tos-cn-beijing.volces.com）

注意：脚本会自动从项目根目录的 .env 文件加载环境变量。
如果 .env 文件中已配置这些变量，无需手动 export。

使用示例：
    # 方式 1：使用 TOS object key（需要 .env 文件中配置 TOS_* 环境变量）
    python test/doubao_av_translate.py --uri "dbqsfy/1.m4a" --source-lang zh --target-lang en --output output.srt

    # 方式 2：使用完整的 HTTP(S) URL（不需要 TOS 环境变量）
    python test/doubao_av_translate.py --uri "https://your-bucket.tos-cn-beijing.volces.com/dbqsfy/1.m4a" --source-lang zh --target-lang en --output output.srt
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 加载 .env 文件（如果存在）
from dubora.config.settings import load_env_file
load_env_file()  # 自动查找项目根目录的 .env 文件

from dubora.infra.storage.tos import TosStorage  # 使用 TOS_* 环境变量生成预签名 URL


def load_keys() -> tuple[str, str]:
    ak = os.getenv("VOLC_ACCESS_KEY")
    sk = os.getenv("VOLC_SECRET_KEY")
    if not ak or not sk:
        raise RuntimeError(
            "环境变量 VOLC_ACCESS_KEY / VOLC_SECRET_KEY 未设置；"
            "请按照火山引擎文档配置 AK/SK。"
        )
    return ak, sk


def ms_to_srt_time(ms: int) -> str:
    """将毫秒转换为 SRT 时间戳格式：HH:MM:SS,mmm"""
    total_ms = int(ms)
    s, ms = divmod(total_ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(subtitles: List[Dict[str, Any]], output_path: Path) -> None:
    """
    将 TranslateAudio 返回的 Subtitles 写成标准 SRT。

    Subtitles 元素结构（参考文档）：
    - StartTime: int
    - EndTime: int
    - Text: 源语言文本
    - Translation: 目标语言文本（本脚本使用该字段）
    """
    lines: List[str] = []
    for i, sub in enumerate(subtitles, start=1):
        start_ms = int(sub.get("StartTime", 0))
        end_ms = int(sub.get("EndTime", 0))
        src_text = (sub.get("Text") or "").strip()
        tgt_text = (sub.get("Translation") or "").strip()

        if not tgt_text and not src_text:
            continue

        text = tgt_text or src_text
        lines.append(str(i))
        lines.append(f"{ms_to_srt_time(start_ms)} --> {ms_to_srt_time(end_ms)}")
        lines.append(text)
        lines.append("")  # 空行分隔

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def build_client():
    """
    构建 TranslateAudio 客户端。

    这里使用 volcengine Python SDK 的通用 Service 接口，
    代码风格参考官方 Go/Java 示例（Action=SubmitAudio/QueryAudio）。
    """
    try:
        from volcengine.ServiceInfo import ServiceInfo
        from volcengine.ApiInfo import ApiInfo
        from volcengine.Credentials import Credentials
        from volcengine.base.Service import Service
    except ImportError:
        print(
            "导入 volcengine 失败；请先安装官方 SDK：\n"
            "  pip install volcengine\n"
            "并参考文档配置 AK/SK: https://www.volcengine.com/docs/4640/78985",
            file=sys.stderr,
        )
        sys.exit(1)

    ak, sk = load_keys()

    # 与文档示例一致：Host = translate.volcengineapi.com, Service = translate, Version = 2020-06-01
    host = "translate.volcengineapi.com"
    region = "cn-north-1"
    service = "translate"
    connection_timeout = 10  # 连接超时（秒）
    socket_timeout = 10      # Socket 超时（秒）

    service_info = ServiceInfo(
        host=host,
        header={"Content-Type": "application/json"},
        credentials=Credentials(ak, sk, service, region),
        connection_timeout=connection_timeout,
        socket_timeout=socket_timeout,
        scheme="https",
    )

    # NOTE: volcengine==1.0.215 的 ApiInfo 签名是：
    # ApiInfo(method, path, query, form, header)
    api_info: Dict[str, ApiInfo] = {
        "SubmitAudio": ApiInfo(
            method="POST",
            path="/",
            # NOTE: 这个 volcengine SDK 的 Request.build() 默认不会对 list 做 doseq 展开，
            # 传 list 会变成 "['SubmitAudio']" 这种字符串，服务端会报 InvalidActionOrVersion。
            query={"Action": "SubmitAudio", "Version": "2020-06-01"},
            form={},
            header={},
        ),
        "QueryAudio": ApiInfo(
            method="POST",
            path="/",
            query={"Action": "QueryAudio", "Version": "2020-06-01"},
            form={},
            header={},
        ),
    }

    client = Service(service_info, api_info)
    return client


def submit_audio(client, uri: str, source_lang: str, target_lang: str) -> str:
    """调用 SubmitAudio，返回 TaskId。"""
    import json

    body = {
        "SourceLanguage": source_lang,
        "TargetLanguage": target_lang,
        "Uri": uri,
    }
    body_str = json.dumps(body, ensure_ascii=False)

    # NOTE: volcengine.base.Service.json() 只返回 response body 字符串（status!=200 会直接 raise）
    resp_text = client.json("SubmitAudio", {}, body_str)
    data = json.loads(resp_text)
    task_id = data.get("TaskId")
    if not task_id:
        raise RuntimeError(f"SubmitAudio response missing TaskId: {data}")
    return task_id


def query_audio_until_done(client, task_id: str, poll_interval: float = 5.0, timeout: int = 3600) -> Dict[str, Any]:
    """轮询 QueryAudio，直到 Status=success 或超时。"""
    import json

    start = time.time()
    while True:
        body = {"TaskId": task_id}
        body_str = json.dumps(body)
        resp_text = client.json("QueryAudio", {}, body_str)
        data = json.loads(resp_text)
        status = data.get("Status") or data.get("status")

        if status == "success":
            return data
        if status in ("failed", "error", "timeout", "cancelled"):
            raise RuntimeError(f"Translate task failed: status={status}, resp={data}")

        if time.time() - start > timeout:
            raise TimeoutError(f"Translate task timeout after {timeout}s, last status={status}")

        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(
        description="豆包音视频翻译测试脚本：TOS Uri → 英文 SRT",
    )
    parser.add_argument(
        "--uri",
        required=True,
        help="音视频文件地址："
             "1) 直接传 http(s) URL，或 "
             "2) 传 TOS object key（例如 dbqsfy/1.m4a，将用 TOS_* 环境变量生成预签名 URL）",
    )
    parser.add_argument(
        "--source-lang",
        default="zh",
        help="源语言（默认 zh）",
    )
    parser.add_argument(
        "--target-lang",
        default="en",
        help="目标语言（默认 en）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="doubao_av.srt",
        help="输出英文字幕文件路径（默认 doubao_av.srt）",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="轮询 QueryAudio 间隔秒数（默认 5s）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="最大等待时间（秒，默认 3600）",
    )

    args = parser.parse_args()

    # 1) 解析 Uri：如果是 http(s)，直接用；否则当作 TOS object key，生成预签名 URL
    uri_arg = args.uri.strip()
    if uri_arg.startswith(("http://", "https://")):
        uri = uri_arg
    else:
        # 视为 TOS object key：dbqsfy/1.m4a
        try:
            storage = TosStorage()
            uri = storage.presigned_get(uri_arg)
        except ValueError as e:
            print(
                f"\n错误：无法生成 TOS 预签名 URL。\n"
                f"原因：{e}\n\n"
                f"解决方案：\n"
                f"1. 设置 TOS 环境变量：\n"
                f"   export TOS_ACCESS_KEY_ID=你的AccessKey\n"
                f"   export TOS_SECRET_ACCESS_KEY=你的SecretKey\n"
                f"   export TOS_BUCKET=pikppo-video\n"
                f"   export TOS_REGION=cn-beijing\n"
                f"   export TOS_ENDPOINT=\"tos-cn-beijing.volces.com\"\n\n"
                f"2. 或者直接传入完整的 HTTP(S) URL：\n"
                f"   python test/doubao_av_translate.py --uri \"https://...\" ...\n",
                file=sys.stderr,
            )
            sys.exit(1)

    client = build_client()

    print(f"[Submit] Uri={uri}, {args.source_lang}→{args.target_lang}")
    task_id = submit_audio(client, uri, args.source_lang, args.target_lang)
    print(f"[Submit] TaskId={task_id}")

    print("[Query] polling until done...")
    result = query_audio_until_done(client, task_id, poll_interval=args.poll_interval, timeout=args.timeout)

    subtitles = result.get("Subtitles") or []
    if not subtitles:
        raise RuntimeError(f"No Subtitles in QueryAudio response: {result}")

    out_path = Path(args.output)
    write_srt(subtitles, out_path)
    print(f"[Done] Wrote English subtitles to: {out_path}")


if __name__ == "__main__":
    main()

