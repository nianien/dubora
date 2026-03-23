"""科大讯飞语音转写 provider（v2 API，urlLink 模式）。

upload(urlLink) → getResult 轮询。
需要环境变量：XFYUN_APPID, XFYUN_API_KEY, XFYUN_API_SECRET
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import random
import string
import sys
import time
import urllib.parse
from typing import Optional

import requests

from .base import ASRProvider

_HOST = "https://office-api-ist-dx.iflyaisol.com"


def _random_str(length=16) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def _local_time_with_tz() -> str:
    local_now = datetime.datetime.now()
    tz_offset = local_now.astimezone().strftime('%z')
    return f"{local_now.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}"


def _make_signature(params: dict, secret: str) -> str:
    """HMAC-SHA1 签名：value URL 编码，key 不编码，按 key 排序。"""
    sign_params = {k: str(v) for k, v in params.items()
                   if k != "signature" and v is not None and str(v).strip()}
    sorted_keys = sorted(sign_params.keys())
    base_string = "&".join(
        f"{k}={urllib.parse.quote(sign_params[k], safe='')}"
        for k in sorted_keys
    )
    sig = hmac.new(secret.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(sig).decode()


def _build_url(endpoint: str, params: dict) -> str:
    qs = urllib.parse.urlencode(params)
    return f"{_HOST}{endpoint}?{qs}"


def _parse_order_result(api_response: dict) -> list[dict]:
    """解析 content.orderResult → sentences 列表。"""
    order_result_str = api_response.get("content", {}).get("orderResult", "{}")
    order_result = json.loads(order_result_str)

    sentences = []
    for item in order_result.get("lattice", []):
        raw = item.get("json_1best", {})
        json_1best = json.loads(raw) if isinstance(raw, str) else raw
        st = json_1best.get("st", {})
        bg_ms = int(st.get("bg", "0"))
        ed_ms = int(st.get("ed", "0"))
        spk_raw = item.get("spk", "0")
        # IST 大模型返回 "段落-0" 格式，标准版返回 "0"
        spk_str = spk_raw.rsplit("-", 1)[-1] if isinstance(spk_raw, str) and "-" in spk_raw else str(spk_raw)
        speaker = int(spk_str) if spk_str.isdigit() else 0

        words = []
        for rt in st.get("rt", []):
            for ws in rt.get("ws", []):
                for cw in ws.get("cw", []):
                    w = cw.get("w", "")
                    if w:
                        words.append({
                            "word": w,
                            "wp": cw.get("wp", "n"),
                            "wb": int(ws.get("wb", 0)),
                            "we": int(ws.get("we", 0)),
                        })

        text = "".join(w["word"] for w in words)
        sentences.append({
            "bg": bg_ms,
            "ed": ed_ms,
            "onebest": text,
            "speaker": speaker,
            "words": words,
        })

    return sentences


class XfyunASRProvider(ASRProvider):
    name = "xfyun"

    def __init__(
        self,
        app_id: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        *,
        speaker_number: int = 0,
        language: str = "autodialect",
    ):
        self.app_id = app_id or os.getenv("XFYUN_APPID", "")
        self.api_key = api_key or os.getenv("XFYUN_API_KEY", "")
        self.api_secret = api_secret or os.getenv("XFYUN_API_SECRET", "")
        if not self.app_id or not self.api_key or not self.api_secret:
            raise RuntimeError("需要 XFYUN_APPID, XFYUN_API_KEY, XFYUN_API_SECRET")

        self.speaker_number = speaker_number
        self.language = language

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        audio_url = audio_input
        duration_ms = kwargs.get("duration_ms", 1)
        sig_random = _random_str()

        # 1. upload (urlLink 模式，讯飞服务器拉取音频)
        print(f"[INFO] 讯飞 ASR urlLink ({duration_ms}ms): {audio_url[:80]}...", file=sys.stderr)
        upload_params = {
            "appId": self.app_id,
            "accessKeyId": self.api_key,
            "dateTime": _local_time_with_tz(),
            "signatureRandom": sig_random,
            "fileSize": "1",
            "fileName": "audio.wav",
            "language": self.language,
            "duration": str(duration_ms),
            "audioMode": "urlLink",
            "audioUrl": audio_url,
        }
        signature = _make_signature(upload_params, self.api_secret)
        url = _build_url("/v2/upload", upload_params)

        resp = requests.post(
            url,
            headers={"Content-Type": "application/octet-stream", "signature": signature},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != "000000":
            raise RuntimeError(f"讯飞 ASR 上传失败: {result.get('descInfo')} ({result.get('code')})")

        order_id = result["content"]["orderId"]
        print(f"[INFO] orderId={order_id}", file=sys.stderr)

        # 2. poll getResult
        for i in range(600):
            query_params = {
                "appId": self.app_id,
                "accessKeyId": self.api_key,
                "dateTime": _local_time_with_tz(),
                "ts": str(int(time.time())),
                "orderId": order_id,
                "signatureRandom": sig_random,
            }
            query_sig = _make_signature(query_params, self.api_secret)
            query_url = _build_url("/v2/getResult", query_params)

            resp = requests.post(
                query_url,
                headers={"Content-Type": "application/json", "signature": query_sig},
                data=json.dumps({}), timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()

            if result.get("code") != "000000":
                raise RuntimeError(f"讯飞 ASR 查询失败: {result.get('descInfo')}")

            status = result["content"]["orderInfo"]["status"]
            if status == 4:
                print("[INFO] 转写完成", file=sys.stderr)
                break
            if status not in (3,):
                raise RuntimeError(f"讯飞 ASR 异常状态: {status}")

            print(f"[INFO] 处理中... ({i + 1})", file=sys.stderr)
            time.sleep(5)
        else:
            raise RuntimeError("讯飞 ASR 轮询超时")

        # 3. parse
        sentences = _parse_order_result(result)

        return {
            "order_id": order_id,
            "duration_ms": result["content"]["orderInfo"].get("originalDuration", 0),
            "sentences": sentences,
            "raw": result,
        }
