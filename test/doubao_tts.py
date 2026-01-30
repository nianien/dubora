#!/usr/bin/env python3
"""
豆包 TTS（文本转语音）测试脚本

用途：
- 输入：文本或 segments JSON 文件
- 调用火山引擎豆包 TTS API
- 输出：合成音频文件（WAV）

参考文档：
- 豆包 TTS API：https://www.volcengine.com/docs/6561/1257584

环境变量：
- VOLC_ACCESS_KEY    火山引擎 AccessKey（必填）
- VOLC_SECRET_KEY    火山引擎 SecretKey（必填）

使用示例：
    # 单段文本合成
    python test/doubao_tts.py --text "你好，世界" --output output.wav

    # 批量合成 segments
    python test/doubao_tts.py --segments en-segments.json --output output.wav

    # 指定语音和语言
    python test/doubao_tts.py --text "Hello world" --voice BV701_streaming --language en

注意：
- 本脚本需要根据实际 API 文档调整以下内容：
  1. API 端点 URL（当前假设为 /api/v1/tts）
  2. 认证方式（当前使用 VOLC_ACCESS_KEY/SECRET_KEY，可能需要改为 X-Api-App-Key/X-Api-Access-Key）
  3. 请求/响应格式（字段名、数据结构）
  4. 语音 ID 列表（BV700_streaming 等）
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def load_keys() -> tuple[str, str]:
    """加载火山引擎 AK/SK。"""
    ak = os.getenv("VOLC_ACCESS_KEY")
    sk = os.getenv("VOLC_SECRET_KEY")
    if not ak or not sk:
        raise RuntimeError(
            "环境变量 VOLC_ACCESS_KEY / VOLC_SECRET_KEY 未设置；"
            "请按照火山引擎文档配置 AK/SK。"
        )
    return ak, sk


def build_tts_client():
    """
    构建豆包 TTS 客户端。
    
    使用 requests 直接调用 HTTP API（参考 doubao ASR 客户端模式）。
    根据文档：https://www.volcengine.com/docs/6561/1257584
    """
    try:
        import requests
    except ImportError:
        print(
            "导入 requests 失败；请先安装：\n"
            "  pip install requests\n",
            file=sys.stderr,
        )
        sys.exit(1)

    ak, sk = load_keys()

    # TTS API 端点（根据文档调整）
    # 注意：实际 API 路径可能需要根据文档调整
    TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"  # 根据文档调整

    class TTSClient:
        def __init__(self, ak: str, sk: str, url: str):
            self.ak = ak
            self.sk = sk
            self.url = url
            self.session = requests.Session()

        def _headers(self, request_id: str) -> Dict[str, str]:
            """
            构建请求头。
            
            注意：根据文档，TTS API 可能使用不同的认证方式：
            - 方式1：使用 X-Api-App-Key / X-Api-Access-Key（类似 ASR）
            - 方式2：使用 VOLC_ACCESS_KEY / VOLC_SECRET_KEY 签名（类似 TranslateAudio）
            
            这里先按方式2实现，如果文档要求方式1，需要调整。
            """
            import uuid
            return {
                "Content-Type": "application/json",
                # 如果文档要求使用 X-Api-App-Key / X-Api-Access-Key，则改为：
                # "X-Api-App-Key": self.ak,
                # "X-Api-Access-Key": self.sk,
                # 如果文档要求使用签名认证，则使用 volcengine SDK 的签名逻辑
                "X-Api-Request-Id": request_id or str(uuid.uuid4()),
            }

        def synthesize(self, text: str, voice_id: str, language: str, format: str, sample_rate: int) -> bytes:
            """
            调用 TTS API 合成文本。
            
            注意：实际 API 请求格式需要根据文档调整。
            """
            import uuid
            request_id = str(uuid.uuid4())

            # 构建请求体（根据文档调整字段名和结构）
            body = {
                "text": text,
                "voice": voice_id,  # 或 "voice_id"，根据文档调整
                "language": language,
                "format": format,
                "sample_rate": sample_rate,
            }

            # 如果文档要求签名认证，需要使用 volcengine SDK 的签名逻辑
            # 这里先使用简单的 requests 调用，实际可能需要签名
            r = self.session.post(
                self.url,
                headers=self._headers(request_id),
                json=body,
                timeout=30,
            )

            # 检查 HTTP 状态码
            if r.status_code >= 400:
                raise RuntimeError(
                    f"TTS API failed: http={r.status_code}, "
                    f"body={r.text[:300]}"
                )

            # 检查业务状态码（如果 API 通过 header 返回）
            status_code = r.headers.get("X-Api-Status-Code")
            if status_code and status_code not in ("20000000", "20000001", "20000002", "20000003"):
                message = r.headers.get("X-Api-Message", "")
                raise RuntimeError(
                    f"TTS API failed: X-Api-Status-Code={status_code}, "
                    f"X-Api-Message={message}, body={r.text[:300]}"
                )

            # 解析响应
            # 如果返回的是 JSON（包含 audio_base64），需要解码
            # 如果返回的是二进制音频数据，直接返回
            content_type = r.headers.get("Content-Type", "")
            if "application/json" in content_type:
                resp_json = r.json()
                # 根据文档调整：可能是 "audio_base64"、"data"、"audio" 等字段
                audio_base64 = resp_json.get("audio_base64") or resp_json.get("data") or resp_json.get("audio")
                if audio_base64:
                    import base64
                    return base64.b64decode(audio_base64)
                else:
                    raise RuntimeError(f"TTS API response missing audio data: {resp_json}")
            else:
                # 直接返回二进制数据
                return r.content

    return TTSClient(ak, sk, TTS_URL)


def synthesize_text(
    client,
    text: str,
    voice_id: str = "BV700_streaming",
    language: str = "zh",
    format: str = "wav",
    sample_rate: int = 24000,
) -> bytes:
    """
    调用 TTS API 合成单段文本。
    
    Args:
        client: TTS 客户端
        text: 要合成的文本
        voice_id: 语音 ID（根据文档选择，常见值：BV700_streaming, BV701_streaming 等）
        language: 语言代码（zh/en 等）
        format: 音频格式（wav/mp3）
        sample_rate: 采样率（常见值：16000, 24000, 48000）
    
    Returns:
        音频文件字节数据
    """
    return client.synthesize(text, voice_id, language, format, sample_rate)


def synthesize_segments(
    segments: List[Dict[str, Any]],
    output_dir: Path,
    voice_id: str = "BV700_streaming",
    language: str = "zh",
    format: str = "wav",
    sample_rate: int = 24000,
) -> Path:
    """
    批量合成 segments，并拼接成完整音频。
    
    Args:
        segments: Segments 列表（每个包含 text, start, end）
        output_dir: 输出目录
        voice_id: 语音 ID
        language: 语言代码
        format: 音频格式
        sample_rate: 采样率
    
    Returns:
        最终音频文件路径
    """
    import subprocess
    import tempfile

    client = build_tts_client()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 临时目录存放单个片段
    segments_dir = output_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    
    segment_files = []
    
    print(f"[TTS] 合成 {len(segments)} 个片段...")
    for i, seg in enumerate(segments):
        text = seg.get("text", "").strip() or seg.get("en_text", "").strip()
        if not text:
            continue
        
        print(f"  [{i+1}/{len(segments)}] {text[:50]}...")
        
        try:
            # 调用 TTS API
            audio_data = synthesize_text(
                client=client,
                text=text,
                voice_id=voice_id,
                language=language,
                format=format,
                sample_rate=sample_rate,
            )
            
            # 保存片段
            segment_file = segments_dir / f"seg_{i:04d}.{format}"
            segment_file.write_bytes(audio_data)
            segment_files.append(str(segment_file))
            
        except Exception as e:
            print(f"  ⚠️  片段 {i+1} 合成失败: {e}")
            continue
    
    if not segment_files:
        raise RuntimeError("没有成功合成任何片段")
    
    # 拼接所有片段
    print(f"[TTS] 拼接 {len(segment_files)} 个片段...")
    output_file = output_dir / "tts_output.wav"
    
    # 使用 ffmpeg 拼接
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_list = f.name
        for seg_file in segment_files:
            f.write(f"file '{seg_file}'\n")
    
    try:
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            "-y",
            str(output_file),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"[TTS] 完成：{output_file}")
        return output_file
    finally:
        os.unlink(concat_list)


def main():
    parser = argparse.ArgumentParser(
        description="豆包 TTS 测试脚本：文本/segments → 音频文件",
    )
    parser.add_argument(
        "--text",
        type=str,
        help="单段文本（如果提供，将只合成这一句）",
    )
    parser.add_argument(
        "--segments",
        type=str,
        help="Segments JSON 文件路径（每段包含 text 或 en_text）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="doubao_tts_output.wav",
        help="输出音频文件路径（默认 doubao_tts_output.wav）",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default="BV700_streaming",
        help="语音 ID（默认 BV700_streaming；常见值：BV700_streaming, BV701_streaming 等，需参考文档）",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="zh",
        help="语言代码（默认 zh）",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="wav",
        choices=["wav", "mp3"],
        help="音频格式（默认 wav）",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=24000,
        help="采样率（默认 24000）",
    )

    args = parser.parse_args()

    if not args.text and not args.segments:
        parser.error("必须提供 --text 或 --segments 参数")

    if args.text and args.segments:
        parser.error("不能同时提供 --text 和 --segments 参数")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client = build_tts_client()

    if args.text:
        # 单段文本合成
        print(f"[TTS] 合成文本: {args.text[:50]}...")
        audio_data = synthesize_text(
            client=client,
            text=args.text,
            voice_id=args.voice,
            language=args.language,
            format=args.format,
            sample_rate=args.sample_rate,
        )
        output_path.write_bytes(audio_data)
        print(f"[Done] 输出: {output_path}")

    else:
        # 批量合成 segments
        segments_path = Path(args.segments)
        if not segments_path.exists():
            parser.error(f"Segments 文件不存在: {segments_path}")

        with open(segments_path, "r", encoding="utf-8") as f:
            segments = json.load(f)

        if not isinstance(segments, list):
            parser.error(f"Segments 文件格式错误：应该是 JSON 数组")

        output_file = synthesize_segments(
            segments=segments,
            output_dir=output_path.parent,
            voice_id=args.voice,
            language=args.language,
            format=args.format,
            sample_rate=args.sample_rate,
        )
        
        # 移动到指定输出路径
        if output_file != output_path:
            output_file.rename(output_path)
        print(f"[Done] 输出: {output_path}")


if __name__ == "__main__":
    main()
