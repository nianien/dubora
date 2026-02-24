#!/usr/bin/env python3
"""
火山引擎 OCR 提取字幕测试脚本

根据文档：https://www.volcengine.com/docs/4/1828818?lang=zh

功能：
- 提交 OCR 提取字幕任务
- 轮询获取任务结果
- 将结果转换为 SRT 字幕文件

使用方法：
    python test/volcengine_ocr_subtitle.py --vid <video_id> --output <output.srt>
"""
import argparse
import json
import time

try:
    from volcengine.base.Service import Service
    from volcengine.ServiceInfo import ServiceInfo
    from volcengine.ApiInfo import ApiInfo
    from volcengine.Credentials import Credentials
except ImportError:
    print("导入 volcengine 失败；请先安装官方 SDK：")
    print("  pip install volcengine")
    print("并参考文档配置 AK/SK: https://www.volcengine.com/docs/4640/78985")
    sys.exit(1)

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dubora.config.settings import load_env_file
from dubora.utils.logger import info, warning, error
from dubora.infra.storage.tos import TosStorage

# 自动加载 .env 文件（如果存在）
load_env_file()


def load_keys():
    """加载环境变量中的密钥。"""
    ak = os.getenv("VOLC_ACCESS_KEY")
    sk = os.getenv("VOLC_SECRET_KEY")
    
    if not ak or not sk:
        error("VOLC_ACCESS_KEY 和 VOLC_SECRET_KEY 环境变量未设置")
        print("\n解决方案：")
        print("1. 在 .env 文件中设置：")
        print("   VOLC_ACCESS_KEY=你的AccessKey")
        print("   VOLC_SECRET_KEY=你的SecretKey")
        print("\n2. 或通过环境变量设置：")
        print("   export VOLC_ACCESS_KEY=你的AccessKey")
        print("   export VOLC_SECRET_KEY=你的SecretKey")
        sys.exit(1)
    
    return ak, sk


def build_vod_client(ak: str, sk: str, region: str = "cn-north-1"):
    """
    构建视频点播（VOD）客户端。
    
    Args:
        ak: Access Key
        sk: Secret Key
        region: 区域（默认 cn-north-1）
    
    Returns:
        Service 客户端
    """
    service = "vod"
    host = "vod.volcengineapi.com"
    
    service_info = ServiceInfo(
        host=host,
        header={"Content-Type": "application/json"},
        credentials=Credentials(ak, sk, service, region),
        connection_timeout=10,
        socket_timeout=10,
        scheme="https",
    )
    
    api_info = {
        "StartExecution": ApiInfo(
            method="POST",
            path="/",
            query={"Action": "StartExecution", "Version": "2025-01-01"},
            form={},
            header={},
        ),
        "GetExecution": ApiInfo(
            method="POST",
            path="/",
            query={"Action": "GetExecution", "Version": "2025-01-01"},
            form={},
            header={},
        ),
    }
    
    client = Service(service_info, api_info)
    return client


def _print_error_help(error_str: str) -> None:
    """根据错误类型打印帮助信息。"""
    error_str_lower = error_str.lower()
    
    if "accessdenied" in error_str_lower or "access denied" in error_str_lower:
        print("\n❌ 错误：访问被拒绝（AccessDenied）")
        print("\n可能的原因：")
        print("1. VOD 服务无法访问 TOS 预签名 URL（权限不足）")
        print("2. TOS bucket 未配置允许 VOD 服务访问")
        print("3. 预签名 URL 的权限设置不正确")
        print("\n解决方案：")
        print("方案 1（推荐）：将视频上传到 VOD 空间，使用 Vid 而不是 DirectUrl")
        print("  - 通过 VOD 控制台上传视频")
        print("  - 获取 Vid（视频 ID）")
        print("  - 使用 --vid 参数而不是 --object-key")
        print("\n方案 2：配置 TOS bucket 允许 VOD 服务访问")
        print("  - 在 TOS 控制台配置 bucket 的跨域和权限设置")
        print("  - 确保 VOD 服务有权限访问该 bucket")
        print("\n方案 3：使用公开可访问的 HTTP(S) URL")
        print("  - 将视频上传到支持公开访问的存储服务（如 CDN）")
        print("  - 使用 --url 参数提供完整的 HTTP(S) URL")
    elif "internalerror" in error_str_lower or "internal error" in error_str_lower:
        print("\n❌ 错误：服务器内部错误（InternalError）")
        print("\n可能的原因：")
        print("1. 文件格式不支持（OCR 只支持视频文件，不支持纯音频文件如 .wav, .mp3）")
        print("2. 视频文件损坏或格式异常")
        print("3. 服务器端临时错误，请稍后重试")
        print("\n建议：")
        print("- 如果使用的是音频文件，请改用视频文件（如 .mp4）")
        print("- 如果使用的是视频文件，请检查文件是否完整且格式正确")
        print("- 等待几分钟后重试")
    else:
        print("\n❌ 未知错误")
        print("请检查错误信息并联系技术支持")


def submit_ocr_task(client: Service, vid: str = None, direct_url: str = None, filename: str = None, space_name: str = None) -> str:
    """
    提交 OCR 提取字幕任务。
    
    Args:
        client: VOD 客户端
        vid: 视频 ID（Vid），如果提供则使用 Vid 方式
        direct_url: 视频直接 URL（可以是 TOS 预签名 URL 或任何可公开访问的 HTTP(S) URL），如果提供则使用 DirectUrl 方式
        filename: 文件名（可选，仅在使用 DirectUrl 时需要）
        space_name: 空间名称（VOD SpaceName，可选，仅在使用 DirectUrl 时需要）
    
    Returns:
        RunId（任务唯一标识）
    
    注意：
        - 必须提供 vid 或 direct_url 之一，不能同时提供
        - 使用 DirectUrl 时，需要提供 filename 和 space_name
        - OCR 提取字幕只支持视频文件（如 MP4、AVI、MOV 等），不支持纯音频文件（如 WAV、MP3 等）
    """
    # 验证参数
    if not vid and not direct_url:
        error("必须提供 vid 或 direct_url 参数之一")
        sys.exit(1)
    
    if vid and direct_url:
        error("不能同时提供 vid 和 direct_url 参数")
        sys.exit(1)
    
    # 构建请求体
    if vid:
        # 使用 Vid 方式
        body = {
            "Input": {
                "Type": "Vid",
                "Vid": vid,
            },
            "Operation": {
                "Type": "Task",
                "Task": {
                    "Type": "Ocr",
                    "Ocr": {},
                },
            },
        }
        info(f"[Submit] Vid={vid}, OCR task")
    else:
        # 使用 DirectUrl 方式
        # 如果没有提供文件名，尝试从 URL 中提取
        if not filename:
            from urllib.parse import urlparse
            parsed = urlparse(direct_url)
            # 从路径中提取文件名（去除查询参数）
            path = parsed.path
            filename = path.split('/')[-1] if path else "video.mp4"
            # 如果仍然没有文件名，使用默认值
            if not filename or filename == "/":
                filename = "video.mp4"
        
        # 检查文件格式：OCR 只支持视频文件
        video_extensions = {'.mp4', '.avi', '.mov', '.flv', '.mkv', '.wmv', '.webm', '.m4v', '.3gp', '.ts', '.mpg', '.mpeg'}
        audio_extensions = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.wma'}
        
        file_ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''
        
        if file_ext in audio_extensions:
            error(f"OCR 提取字幕不支持纯音频文件: {filename}")
            print(f"\n错误：OCR 提取字幕功能只支持视频文件，不支持音频文件。")
            print(f"\n支持的视频格式：{', '.join(sorted(video_extensions))}")
            print(f"\n解决方案：")
            print(f"1. 使用视频文件（如 .mp4）而不是音频文件（如 .wav）")
            print(f"2. 如果只有音频文件，请先使用其他工具（如 ffmpeg）将音频与视频合并")
            print(f"3. 或者使用 ASR（语音识别）功能来处理音频文件")
            sys.exit(1)
        
        if file_ext and file_ext not in video_extensions:
            warning(f"文件格式 {file_ext} 可能不被支持，建议使用标准视频格式（如 .mp4）")
        
        # 构建 DirectUrl 对象
        direct_url_obj = {
            "Url": direct_url,
            "FileName": filename,
        }
        
        # 如果提供了 SpaceName，添加到对象中
        if space_name:
            direct_url_obj["SpaceName"] = space_name
        
        body = {
            "Input": {
                "Type": "DirectUrl",
                "DirectUrl": direct_url_obj,
            },
            "Operation": {
                "Type": "Task",
                "Task": {
                    "Type": "Ocr",
                    "Ocr": {},
                },
            },
        }
        info(f"[Submit] DirectUrl={direct_url}, FileName={filename}, SpaceName={space_name or 'N/A'}, OCR task")
    
    
    try:
        resp = client.json("StartExecution", {}, json.dumps(body))
        result = json.loads(resp)
        
        if "Result" in result and "RunId" in result["Result"]:
            run_id = result["Result"]["RunId"]
            info(f"[Submit] Success, RunId={run_id}")
            return run_id
        else:
            error(f"[Submit] Failed: {result}")
            # 直接显示服务端返回的错误信息
            if "ResponseMetadata" in result and "Error" in result["ResponseMetadata"]:
                error_info = result["ResponseMetadata"]["Error"]
                error_code = error_info.get("Code", "Unknown")
                error_msg = error_info.get("Message", "No message")
                print(f"\n服务端错误：")
                print(f"  Code: {error_code}")
                print(f"  Message: {error_msg}")
                
                # 针对常见错误提供解决方案
                if "RequestForbidden" in error_code or "Permission denied" in error_msg:
                    print(f"\n❌ 权限错误：请求被拒绝")
                    print(f"\n可能的原因：")
                    print(f"1. VOD OCR 功能未开通或未申请白名单")
                    print(f"2. 账号权限不足，无法使用 OCR 功能")
                    print(f"3. 区域配置不正确（当前区域：cn-north-1）")
                    print(f"\n解决方案：")
                    print(f"1. 联系火山引擎技术支持团队申请开通 VOD OCR 功能")
                    print(f"2. 确认账号是否有使用 OCR 功能的权限")
                    print(f"3. 检查区域配置是否正确（可能需要使用其他区域）")
                    print(f"4. 参考文档：https://www.volcengine.com/docs/4/1828818?lang=zh")
            sys.exit(1)
    except Exception as e:
        error(f"[Submit] Exception: {e}")
        # 尝试解析错误响应中的 JSON
        error_str = str(e)
        if error_str.startswith("b'") and "ResponseMetadata" in error_str:
            try:
                # 移除 b' 前缀和 ' 后缀，然后解析 JSON
                json_str = error_str[2:-1].replace("\\'", "'")
                error_data = json.loads(json_str)
                if "ResponseMetadata" in error_data and "Error" in error_data["ResponseMetadata"]:
                    error_info = error_data["ResponseMetadata"]["Error"]
                    error_code = error_info.get("Code", "Unknown")
                    error_msg = error_info.get("Message", "No message")
                    print(f"\n服务端错误：")
                    print(f"  Code: {error_code}")
                    print(f"  Message: {error_msg}")
                    
                    # 针对常见错误提供解决方案
                    if "RequestForbidden" in error_code or "Permission denied" in error_msg:
                        print(f"\n❌ 权限错误：请求被拒绝")
                        print(f"\n可能的原因：")
                        print(f"1. VOD OCR 功能未开通或未申请白名单")
                        print(f"2. 账号权限不足，无法使用 OCR 功能")
                        print(f"3. 区域配置不正确（当前区域：cn-north-1）")
                        print(f"\n解决方案：")
                        print(f"1. 联系火山引擎技术支持团队申请开通 VOD OCR 功能")
                        print(f"2. 确认账号是否有使用 OCR 功能的权限")
                        print(f"3. 检查区域配置是否正确（可能需要使用其他区域）")
                        print(f"4. 参考文档：https://www.volcengine.com/docs/4/1828818?lang=zh")
            except:
                pass
        sys.exit(1)


def _print_error_help(error_str: str) -> None:
    """根据错误类型打印帮助信息。"""
    error_str_lower = error_str.lower()
    
    if "accessdenied" in error_str_lower or "access denied" in error_str_lower:
        print("\n❌ 错误：访问被拒绝（AccessDenied）")
        print("\n可能的原因：")
        print("1. VOD 服务无法访问 TOS 预签名 URL（权限不足）")
        print("2. TOS bucket 未配置允许 VOD 服务访问")
        print("3. 预签名 URL 的权限设置不正确")
        print("\n解决方案：")
        print("方案 1（推荐）：将视频上传到 VOD 空间，使用 Vid 而不是 DirectUrl")
        print("  - 通过 VOD 控制台上传视频")
        print("  - 获取 Vid（视频 ID）")
        print("  - 使用 --vid 参数而不是 --object-key")
        print("\n方案 2：配置 TOS bucket 允许 VOD 服务访问")
        print("  - 在 TOS 控制台配置 bucket 的跨域和权限设置")
        print("  - 确保 VOD 服务有权限访问该 bucket")
        print("\n方案 3：使用公开可访问的 HTTP(S) URL")
        print("  - 将视频上传到支持公开访问的存储服务（如 CDN）")
        print("  - 使用 --url 参数提供完整的 HTTP(S) URL")
    elif "internalerror" in error_str_lower or "internal error" in error_str_lower:
        print("\n❌ 错误：服务器内部错误（InternalError）")
        print("\n可能的原因：")
        print("1. 文件格式不支持（OCR 只支持视频文件，不支持纯音频文件如 .wav, .mp3）")
        print("2. 视频文件损坏或格式异常")
        print("3. 服务器端临时错误，请稍后重试")
        print("\n建议：")
        print("- 如果使用的是音频文件，请改用视频文件（如 .mp4）")
        print("- 如果使用的是视频文件，请检查文件是否完整且格式正确")
        print("- 等待几分钟后重试")
    else:
        print("\n❌ 未知错误")
        print("请检查错误信息并联系技术支持")


def query_task_result(client: Service, run_id: str) -> dict:
    """
    查询任务结果。
    
    Args:
        client: VOD 客户端
        run_id: 任务 RunId
    
    Returns:
        任务结果字典（如果完成），否则返回 None
    """
    body = {
        "RunId": run_id,
    }
    
    try:
        resp = client.json("GetExecution", {}, json.dumps(body))
        result = json.loads(resp)
        
        if "Result" in result:
            status = result["Result"].get("Status", "")
            if status == "Success":
                return result["Result"]
            elif status == "Failed":
                error(f"[Query] Task failed: {result['Result']}")
                sys.exit(1)
            else:
                # 任务还在处理中
                return None
        else:
            error(f"[Query] Invalid response: {result}")
            return None
    except Exception as e:
        error(f"[Query] Exception: {e}")
        return None


def wait_for_task(client: Service, run_id: str, max_wait_seconds: int = 3600) -> dict:
    """
    轮询等待任务完成。
    
    Args:
        client: VOD 客户端
        run_id: 任务 RunId
        max_wait_seconds: 最大等待时间（秒，默认 3600）
    
    Returns:
        任务结果字典
    """
    start_time = time.time()
    poll_interval = 5  # 每 5 秒轮询一次
    
    info(f"[Wait] Polling for task completion (RunId={run_id})...")
    
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait_seconds:
            error(f"[Wait] Timeout after {max_wait_seconds} seconds")
            sys.exit(1)
        
        result = query_task_result(client, run_id)
        if result is not None:
            info(f"[Wait] Task completed in {elapsed:.1f} seconds")
            return result
        
        info(f"[Wait] Task still processing... (elapsed: {elapsed:.1f}s)")
        time.sleep(poll_interval)


def ocr_result_to_srt(ocr_result: dict, output_path: Path) -> None:
    """
    将 OCR 结果转换为 SRT 字幕文件。
    
    Args:
        ocr_result: OCR 任务结果（包含 Output.Task.Ocr）
        output_path: 输出 SRT 文件路径
    """
    # 提取 OCR 数据
    ocr_data = ocr_result.get("Output", {}).get("Task", {}).get("Ocr", {})
    texts = ocr_data.get("Texts", [])
    
    if not texts:
        warning("[Convert] No texts found in OCR result")
        return
    
    info(f"[Convert] Converting {len(texts)} text segments to SRT...")
    
    # 转换为 SRT 格式
    srt_lines = []
    for i, text_item in enumerate(texts, start=1):
        text = text_item.get("Text", "").strip()
        start_sec = text_item.get("Start", 0.0)
        end_sec = text_item.get("End", 0.0)
        
        if not text:
            continue
        
        # 转换时间格式：秒 -> SRT 时间格式 (HH:MM:SS,mmm)
        start_time = format_srt_time(start_sec)
        end_time = format_srt_time(end_sec)
        
        # SRT 格式：
        # 序号
        # 开始时间 --> 结束时间
        # 文本内容
        # 空行
        srt_lines.append(f"{i}")
        srt_lines.append(f"{start_time} --> {end_time}")
        srt_lines.append(text)
        srt_lines.append("")  # 空行
    
    # 写入文件
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))
    
    info(f"[Convert] Saved SRT file to: {output_path}")


def format_srt_time(seconds: float) -> str:
    """
    将秒数转换为 SRT 时间格式 (HH:MM:SS,mmm)。
    
    Args:
        seconds: 秒数（浮点数）
    
    Returns:
        SRT 时间格式字符串，如 "00:00:01,440"
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def main():
    parser = argparse.ArgumentParser(
        description="火山引擎 OCR 提取字幕测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 方式 1：使用 Vid（最推荐，视频已上传到 VOD 空间）
  python test/volcengine_ocr_subtitle.py --vid "v02399g10001xxxxxxxxxxxxxxxxxxxx" --output output.srt
  
  # 方式 2：使用 TOS object key
  python test/volcengine_ocr_subtitle.py --object-key "dbqsfy/1.mp4" --output output.srt
  
  # 方式 3：使用直接 URL（视频在外部存储）
  python test/volcengine_ocr_subtitle.py --url "https://example.com/video.mp4" --output output.srt

关于 Vid：
  Vid 是火山引擎视频点播（VOD）中视频文件的唯一标识符。
  如果视频已上传到 VOD 空间，使用 --vid 参数是最简单的方式。
  
  获取 Vid 的方法：
  1. 通过 VOD 控制台：上传视频后，在媒资列表中可以看到每个视频的 Vid
  2. 通过 API：使用 VOD 上传 API 上传视频后，响应中会返回 Vid

关于 TOS object key：
  TOS object key 是视频在火山引擎对象存储（TOS）中的路径。
  例如：dbqsfy/1.mp4 表示在 TOS bucket 中的 dbqsfy/1.mp4 文件。
  
  脚本会自动使用 TOS 配置生成预签名 URL，然后提交给 OCR API。
  
  环境变量（自动从 .env 加载）：
  - TOS_ACCESS_KEY_ID: TOS AccessKey
  - TOS_SECRET_ACCESS_KEY: TOS SecretKey
  - TOS_BUCKET: TOS 桶名（默认：pikppo-video）
  - TOS_REGION: TOS 区域（默认：cn-beijing）
  - TOS_ENDPOINT: TOS 端点（默认：tos-cn-beijing.volces.com）

环境变量：
  VOLC_ACCESS_KEY: 火山引擎 Access Key
  VOLC_SECRET_KEY: 火山引擎 Secret Key
        """,
    )
    
    parser.add_argument(
        "--vid",
        dest="vid",
        help="视频 ID（Vid）- 视频已上传到 VOD 空间时使用（推荐）",
    )
    
    parser.add_argument(
        "--object-key",
        dest="object_key",
        help="TOS object key（例如：dbqsfy/1.mp4）- 脚本会自动生成预签名 URL",
    )
    
    parser.add_argument(
        "--url",
        dest="direct_url",
        help="视频直接 URL - 如果视频不在 TOS 中，可以使用可公开访问的 HTTP(S) URL",
    )
    
    parser.add_argument(
        "--output",
        default="ocr_subtitle.srt",
        help="输出 SRT 文件路径（默认: ocr_subtitle.srt）",
    )
    
    parser.add_argument(
        "--region",
        default="cn-north-1",
        help="区域（默认: cn-north-1）",
    )
    
    parser.add_argument(
        "--max-wait",
        type=int,
        default=3600,
        help="最大等待时间（秒，默认: 3600）",
    )
    
    args = parser.parse_args()
    
    # 验证参数
    if not args.vid and not args.object_key and not args.direct_url:
        error("必须提供 --vid、--object-key 或 --url 参数之一")
        parser.print_help()
        sys.exit(1)
    
    # 检查是否同时提供了多个参数
    provided_count = sum([bool(args.vid), bool(args.object_key), bool(args.direct_url)])
    if provided_count > 1:
        error("不能同时提供多个输入参数（--vid、--object-key、--url 只能选一个）")
        parser.print_help()
        sys.exit(1)
    
    # 确定视频输入方式
    video_url = None
    video_filename = None
    video_space_name = None
    
    if args.vid:
        # 使用 Vid 方式（最简单，推荐）
        vid = args.vid
        info(f"[Input] Using Vid: {vid}")
    elif args.object_key:
        # 使用 TOS object key，生成预签名 URL
        try:
            # 确保 TOS 环境变量已加载（再次检查）
            tos_ak = os.getenv("TOS_ACCESS_KEY_ID")
            tos_sk = os.getenv("TOS_SECRET_ACCESS_KEY")
            if not tos_ak or not tos_sk:
                error("TOS_ACCESS_KEY_ID 或 TOS_SECRET_ACCESS_KEY 环境变量未设置")
                print("\n解决方案：")
                print("1. 在项目根目录的 .env 文件中配置：")
                print("   TOS_ACCESS_KEY_ID=你的AccessKey")
                print("   TOS_SECRET_ACCESS_KEY=你的SecretKey")
                print("   TOS_BUCKET=pikppo-video")
                print("   TOS_REGION=cn-beijing")
                print("   TOS_ENDPOINT=tos-cn-beijing.volces.com")
                print("\n2. 或通过环境变量设置：")
                print("   export TOS_ACCESS_KEY_ID=你的AccessKey")
                print("   export TOS_SECRET_ACCESS_KEY=你的SecretKey")
                sys.exit(1)
            
            storage = TosStorage()
            
            # 检查对象是否存在
            if not storage.exists(args.object_key):
                error(f"TOS 对象不存在: {args.object_key}")
                print(f"\n错误：指定的 TOS object key 不存在。")
                print(f"\n可能的原因：")
                print(f"1. 文件尚未上传到 TOS")
                print(f"2. object key 路径不正确")
                print(f"3. TOS bucket 配置不正确（当前配置：{storage.config.bucket}）")
                print(f"\n解决方案：")
                print(f"1. 检查 TOS bucket '{storage.config.bucket}' 中是否存在该文件")
                print(f"2. 如果文件不存在，请先上传文件到 TOS")
                print(f"3. 确认 object key 路径是否正确（例如：dbqsfy/1.mp4）")
                print(f"4. 或者使用 --url 参数直接提供视频的 HTTP(S) URL")
                sys.exit(1)
            
            video_url = storage.presigned_get(args.object_key, expires_seconds=36000)
            # 从 object_key 中提取文件名
            video_filename = args.object_key.split('/')[-1] if '/' in args.object_key else args.object_key
            # 如果没有提供 space_name，尝试从 TOS bucket 配置中获取（VOD 空间名称通常与 bucket 名称相同）
            video_space_name = getattr(args, 'space_name', None) or storage.config.bucket
            info(f"[TOS] Generated presigned URL for object key: {args.object_key}")
        except Exception as e:
            error(f"[TOS] Failed to generate presigned URL: {e}")
            print("\n解决方案：")
            print("1. 确保 .env 文件中配置了 TOS 相关环境变量：")
            print("   TOS_ACCESS_KEY_ID=你的AccessKey")
            print("   TOS_SECRET_ACCESS_KEY=你的SecretKey")
            print("   TOS_BUCKET=pikppo-video")
            print("   TOS_REGION=cn-beijing")
            print("   TOS_ENDPOINT=tos-cn-beijing.volces.com")
            sys.exit(1)
    else:
        # 直接使用提供的 URL
        video_url = args.direct_url
    
    # 加载密钥
    ak, sk = load_keys()
    
    # 构建客户端
    client = build_vod_client(ak, sk, region=args.region)
    
    # 提交任务
    if args.vid:
        # 使用 Vid 方式
        run_id = submit_ocr_task(client, vid=args.vid)
    else:
        # 使用 DirectUrl 方式
        final_space_name = video_space_name if args.object_key else getattr(args, 'space_name', None)
        run_id = submit_ocr_task(
            client, 
            direct_url=video_url, 
            filename=video_filename,
            space_name=final_space_name
        )
    
    # 等待任务完成
    result = wait_for_task(client, run_id, max_wait_seconds=args.max_wait)
    
    # 转换为 SRT
    output_path = Path(args.output)
    ocr_result_to_srt(result, output_path)
    
    info(f"[Done] OCR subtitle extraction completed: {output_path}")


if __name__ == "__main__":
    main()
