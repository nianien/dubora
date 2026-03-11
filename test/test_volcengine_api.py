#!/usr/bin/env python3
"""
火山引擎 API 测试脚本

根据文档：https://www.volcengine.com/docs/6561/1598757?lang=zh

功能：
- 输入文本，调用火山引擎 API
- 返回响应数据，格式：
  {
      "code": 0,
      "message": "",
      "data": null,
      "sentence": <object>
  }

使用方法：
    python test/test_volcengine_api.py --text "测试文本"
    
环境变量：
- VOLC_ACCESS_KEY    火山引擎 AccessKey（必填）
- VOLC_SECRET_KEY    火山引擎 SecretKey（必填）
- VOLC_APP_KEY       火山引擎 AppKey（可选，根据文档要求）
- VOLC_APP_ID        火山引擎 AppID（可选，根据文档要求）
"""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    print("导入 requests 失败；请先安装：\n  pip install requests\n", file=sys.stderr)
    sys.exit(1)

from dubora_core.config.settings import load_env_file

# 自动加载 .env 文件（如果存在）
load_env_file()


def load_keys() -> tuple[str, str, Optional[str], Optional[str]]:
    """
    加载火山引擎认证信息。
    
    支持两种认证方式：
    1. DOUBAO_APPID / DOUBAO_ACCESS_TOKEN（豆包 API，类似 ASR）
    2. VOLC_ACCESS_KEY / VOLC_SECRET_KEY（通用火山引擎 API）
    """
    # 优先使用豆包 API 认证（如果存在）
    appid = os.getenv("DOUBAO_APPID")
    access_token = os.getenv("DOUBAO_ACCESS_TOKEN")
    
    if appid and access_token:
        return appid, access_token, None, None
    
    # 否则使用通用火山引擎认证
    ak = os.getenv("VOLC_ACCESS_KEY")
    sk = os.getenv("VOLC_SECRET_KEY")
    app_key = os.getenv("VOLC_APP_KEY")
    app_id = os.getenv("VOLC_APP_ID")
    
    if not ak or not sk:
        raise RuntimeError(
            "环境变量未设置；请选择以下认证方式之一：\n"
            "方式1（豆包 API）：\n"
            "  DOUBAO_APPID=你的AppID\n"
            "  DOUBAO_ACCESS_TOKEN=你的AccessToken\n"
            "方式2（通用火山引擎 API）：\n"
            "  VOLC_ACCESS_KEY=你的AccessKey\n"
            "  VOLC_SECRET_KEY=你的SecretKey\n"
            "参考文档：https://www.volcengine.com/docs/4640/78985"
        )
    
    return ak, sk, app_key, app_id


def build_client():
    """
    构建火山引擎 TTS API 客户端。
    
    根据文档：https://www.volcengine.com/docs/6561/1598757?lang=zh
    
    这是单向流式 TTS API，支持流式输出音频数据。
    """
    ak, sk, app_key, app_id = load_keys()
    
    # API 端点（根据文档）
    # 单向流式 TTS API
    API_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    
    class VolcengineAPIClient:
        def __init__(self, ak: str, sk: str, app_key: Optional[str] = None, app_id: Optional[str] = None):
            self.ak = ak
            self.sk = sk
            self.app_key = app_key
            self.app_id = app_id
            self.session = requests.Session()
            self.api_url = API_URL
        
        def _headers(self, request_id: str, resource_id: Optional[str] = None) -> Dict[str, str]:
            """
            构建请求头。
            
            根据文档 6561/1598757，TTS API 使用：
            - X-Api-App-Id: APP ID（从控制台获取）
            - X-Api-Access-Key: Access Token（从控制台获取）
            - X-Api-Resource-Id: 资源 ID（如 seed-tts-1.0, seed-tts-2.0 等）
            - X-Api-Request-Id: 请求 ID（可选，UUID）
            """
            headers = {
                "Content-Type": "application/json",
            }
            
            # 如果 ak 是 appid，sk 是 access_token（豆包 API 模式）
            # 使用 X-Api-App-Id / X-Api-Access-Key
            if self.app_key is None and self.app_id is None:
                # 豆包 API 模式：ak 是 appid，sk 是 access_token
                headers["X-Api-App-Id"] = self.ak
                headers["X-Api-Access-Key"] = self.sk
            
            # 如果提供了 app_key 和 app_id
            elif self.app_key and self.app_id:
                headers["X-Api-App-Id"] = self.app_id
                headers["X-Api-Access-Key"] = self.app_key
            
            # 如果只提供了 app_id
            elif self.app_id:
                headers["X-Api-App-Id"] = self.app_id
                headers["X-Api-Access-Key"] = self.ak  # 使用 ak 作为 access_key
            
            # 如果提供了 resource_id（TTS API 必需）
            if resource_id:
                headers["X-Api-Resource-Id"] = resource_id
            
            # 添加请求 ID（可选，但建议添加）
            if request_id:
                headers["X-Api-Request-Id"] = request_id
            
            return headers
        
        def call_api(
            self,
            text: str,
            speaker: str = "zh_female_shuangkuaisisi_moon_bigtts",  # 默认音色
            resource_id: Optional[str] = "seed-tts-1.0",  # 默认资源 ID
            format: str = "mp3",
            sample_rate: int = 24000,
            enable_timestamp: bool = False,
            enable_subtitle: bool = False,
            **kwargs
        ) -> Dict[str, Any]:
            """
            调用火山引擎 TTS API（流式）。
            
            根据文档：https://www.volcengine.com/docs/6561/1598757?lang=zh
            
            Args:
                text: 输入文本
                speaker: 发音人（音色 ID）
                resource_id: 资源 ID（如 "seed-tts-1.0", "seed-tts-2.0" 等）
                format: 音频格式（mp3/ogg_opus/pcm）
                sample_rate: 采样率（可选值：8000,16000,22050,24000,32000,44100,48000）
                enable_timestamp: 是否启用时间戳（TTS1.0 支持）
                enable_subtitle: 是否启用字幕（TTS2.0/ICL2.0 支持）
                **kwargs: 其他参数（根据文档调整）
            
            Returns:
                响应数据列表（流式响应），每个元素格式：
                - 音频数据：{"code": 0, "message": "", "data": "base64音频数据"}
                - 文本数据：{"code": 0, "message": "", "data": null, "sentence": <object>}
                - 结束：{"code": 20000000, "message": "ok", "data": null, "usage": {...}}
            """
            request_id = str(uuid.uuid4())
            
            # 构建请求体（根据文档）
            body = {
                "user": {
                    "uid": kwargs.get("uid", "test_user")
                },
                "req_params": {
                    "text": text,
                    "speaker": speaker,
                    "audio_params": {
                        "format": format,
                        "sample_rate": sample_rate,
                    }
                }
            }
            
            # 如果启用时间戳
            if enable_timestamp:
                body["req_params"]["audio_params"]["enable_timestamp"] = True
            
            # 如果启用字幕
            if enable_subtitle:
                body["req_params"]["audio_params"]["enable_subtitle"] = True
            
            # 合并其他参数到 req_params
            if "model" in kwargs:
                body["req_params"]["model"] = kwargs["model"]
            if "ssml" in kwargs:
                body["req_params"]["ssml"] = kwargs["ssml"]
            if "additions" in kwargs:
                body["req_params"]["additions"] = kwargs["additions"]
            
            try:
                # 流式请求（stream=True）
                response = self.session.post(
                    self.api_url,
                    headers=self._headers(request_id, resource_id=resource_id),
                    json=body,
                    stream=True,  # 重要：流式响应
                    timeout=60,
                )
                
                # 打印详细的错误信息（用于调试）
                if response.status_code >= 400:
                    error_text = response.text if hasattr(response, 'text') else ""
                    print(f"\n❌ API 调用失败，详细信息：")
                    print(f"HTTP 状态码: {response.status_code}")
                    print(f"响应头: {json.dumps(dict(response.headers), indent=2, ensure_ascii=False)}")
                    print(f"响应体: {error_text[:1000]}")
                    
                    # 尝试解析 JSON 错误信息
                    try:
                        error_json = response.json()
                        print(f"解析后的错误信息: {json.dumps(error_json, indent=2, ensure_ascii=False)}")
                    except:
                        pass
                    
                    response.raise_for_status()
                
                # 检查 X-Api-Status-Code header（如果 API 使用这种方式）
                status_code = response.headers.get("X-Api-Status-Code")
                if status_code:
                    ok_codes = {"20000000", "20000001", "20000002", "20000003"}
                    if status_code not in ok_codes:
                        message = response.headers.get("X-Api-Message", "Unknown error")
                        raise RuntimeError(
                            f"API call failed: X-Api-Status-Code={status_code}, "
                            f"X-Api-Message={message}, "
                            f"http={response.status_code}"
                        )
                
                # 流式读取响应
                results = []
                audio_chunks = []  # 收集音频数据
                
                print("\n📡 开始接收流式响应...")
                for line in response.iter_lines():
                    if not line:
                        continue
                    
                    try:
                        # 解析 JSON 行
                        result = json.loads(line)
                        results.append(result)
                        
                        # 检查响应类型
                        code = result.get("code", -1)
                        
                        # 音频数据
                        if "data" in result and result.get("data") is not None:
                            data = result.get("data")
                            if isinstance(data, str) and data:  # base64 音频数据
                                audio_chunks.append(data)
                                print(f"  📦 收到音频数据块 (base64 长度: {len(data)})")
                        
                        # 文本数据（时间戳/字幕）
                        if "sentence" in result:
                            sentence = result.get("sentence")
                            print(f"  📝 收到文本数据: {json.dumps(sentence, indent=2, ensure_ascii=False)}")
                        
                        # 结束标记
                        if code == 20000000:
                            print(f"  ✅ 合成完成 (code: {code}, message: {result.get('message', '')})")
                            if "usage" in result:
                                print(f"  📊 用量信息: {result.get('usage')}")
                            break
                        
                        # 错误码
                        if code != 0 and code != 20000000:
                            message = result.get("message", "Unknown error")
                            raise RuntimeError(f"API call failed: code={code}, message={message}")
                    
                    except json.JSONDecodeError as e:
                        print(f"  ⚠️  无法解析 JSON 行: {line[:100]}")
                        continue
                
                # 返回所有结果
                return {
                    "results": results,
                    "audio_chunks": audio_chunks,
                    "total_chunks": len(audio_chunks),
                }
                
            except requests.exceptions.HTTPError as e:
                # HTTP 错误，显示详细错误信息
                error_msg = f"HTTP request failed: {e}"
                if hasattr(e, 'response') and hasattr(e.response, 'text'):
                    error_msg += f"\n响应体: {e.response.text[:500]}"
                raise RuntimeError(error_msg)
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"HTTP request failed: {e}")
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Failed to parse JSON response: {e}")
    
    return VolcengineAPIClient(ak, sk, app_key, app_id)


def main():
    parser = argparse.ArgumentParser(
        description="火山引擎 API 测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：
  # 基本调用
  python test/test_volcengine_api.py --text "测试文本"
  
  # 指定输出文件
  python test/test_volcengine_api.py --text "测试文本" --output result.json
  
  # 传递额外参数（根据文档调整）
  python test/test_volcengine_api.py --text "测试文本" --param key value

环境变量：
  VOLC_ACCESS_KEY: 火山引擎 Access Key
  VOLC_SECRET_KEY: 火山引擎 Secret Key
  VOLC_APP_KEY: 火山引擎 App Key（可选）
  VOLC_APP_ID: 火山引擎 App ID（可选）

参考文档：
  https://www.volcengine.com/docs/6561/1598757?lang=zh
        """,
    )
    
    parser.add_argument(
        "--text",
        type=str,
        required=True,
        help="输入文本",
    )
    
    parser.add_argument(
        "--output",
        type=str,
        help="输出文件路径（JSON 格式）",
    )
    
    parser.add_argument(
        "--param",
        nargs=2,
        action="append",
        metavar=("KEY", "VALUE"),
        help="额外参数（可多次使用）",
    )
    
    parser.add_argument(
        "--resource-id",
        type=str,
        default="seed-tts-1.0",
        help="资源 ID（默认: seed-tts-1.0，可选: seed-tts-2.0, seed-icl-1.0 等）",
    )
    
    parser.add_argument(
        "--speaker",
        type=str,
        default="zh_female_shuangkuaisisi_moon_bigtts",
        help="发音人（音色 ID，默认: zh_female_shuangkuaisisi_moon_bigtts）",
    )
    
    parser.add_argument(
        "--format",
        type=str,
        default="mp3",
        choices=["mp3", "ogg_opus", "pcm"],
        help="音频格式（默认: mp3）",
    )
    
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=24000,
        choices=[8000, 16000, 22050, 24000, 32000, 44100, 48000],
        help="采样率（默认: 24000）",
    )
    
    parser.add_argument(
        "--enable-timestamp",
        action="store_true",
        help="启用时间戳（TTS1.0 支持）",
    )
    
    parser.add_argument(
        "--enable-subtitle",
        action="store_true",
        help="启用字幕（TTS2.0/ICL2.0 支持）",
    )
    
    parser.add_argument(
        "--api-url",
        type=str,
        help="API 端点 URL（覆盖默认值）",
    )
    
    args = parser.parse_args()
    
    try:
        # 构建客户端
        client = build_client()
        
        # 如果指定了自定义 API URL，覆盖默认值
        if args.api_url:
            client.api_url = args.api_url
        
        # 准备额外参数
        extra_params = {}
        if args.param:
            for key, value in args.param:
                extra_params[key] = value
        
        # 调用 API
        print(f"调用火山引擎 TTS API...")
        print(f"API 端点: {client.api_url}")
        print(f"输入文本: {args.text}")
        print(f"资源 ID: {args.resource_id}")
        print(f"音色: {args.speaker}")
        print(f"音频格式: {args.format}, 采样率: {args.sample_rate}")
        if args.enable_timestamp:
            print(f"启用时间戳: True")
        if args.enable_subtitle:
            print(f"启用字幕: True")
        if extra_params:
            print(f"额外参数: {extra_params}")
        
        result = client.call_api(
            args.text,
            speaker=args.speaker,
            resource_id=args.resource_id,
            format=args.format,
            sample_rate=args.sample_rate,
            enable_timestamp=args.enable_timestamp,
            enable_subtitle=args.enable_subtitle,
            **extra_params
        )
        
        # 打印结果摘要
        print("\n" + "=" * 60)
        print("API 响应摘要:")
        print("=" * 60)
        print(f"总响应数: {len(result.get('results', []))}")
        print(f"音频数据块数: {result.get('total_chunks', 0)}")
        
        # 保存完整结果到文件
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"\n完整结果已保存到: {output_path}")
        
        # 如果有音频数据，保存音频文件
        audio_chunks = result.get("audio_chunks", [])
        if audio_chunks:
            import base64
            # 合并所有音频数据块
            audio_base64 = "".join(audio_chunks)
            audio_bytes = base64.b64decode(audio_base64)
            
            # 保存音频文件
            audio_output = args.output.replace(".json", f".{args.format}") if args.output else f"output.{args.format}"
            audio_path = Path(audio_output)
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
            print(f"音频文件已保存到: {audio_path} ({len(audio_bytes)} bytes)")
        
        # 打印所有响应结果
        print("\n" + "=" * 60)
        print("所有响应结果:")
        print("=" * 60)
        for i, res in enumerate(result.get("results", [])):
            print(f"\n响应 {i+1}:")
            print(json.dumps(res, indent=2, ensure_ascii=False))
        
        return 0
        
    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
