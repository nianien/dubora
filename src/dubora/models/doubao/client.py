"""
Doubao ASR 客户端：纯 API 调用（HTTP 客户端）

职责：
- submit / query / poll
- headers / request_id / 错误码

禁止：
- ❌ preset 选择逻辑
- ❌ speaker 解析
- ❌ SRT 生成
- ❌ 视频/音频处理
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

import requests

from .request_types import DoubaoASRRequest


class DoubaoASRClient:
    """
    Standard (async) submit/query client:
      POST /api/v3/auc/bigmodel/submit  (body is JSON; response body empty; status in headers)
      POST /api/v3/auc/bigmodel/query   (body is {}; response body JSON with result/utterances)

    Docs: https://www.volcengine.com/docs/6561/1354868
    """

    SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
    QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"

    def __init__(self, app_key: str, access_key: str, timeout_s: int = 60):
        self.app_key = app_key
        self.access_key = access_key
        self.timeout_s = timeout_s
        self.session = requests.Session()

    def _headers(self, resource_id: str, request_id: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Api-App-Key": self.app_key,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": resource_id,  # ✅ 只在 header
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
        }

    def submit(self, req: DoubaoASRRequest, *, resource_id: str) -> str:
        """
        最优方案：submit 只接收强类型请求对象。
        - body = req.to_dict()
        - header 带 resource_id
        """
        request_id = str(uuid.uuid4())

        body = req.to_dict()  # ✅ 自动去 None；层级/字段位置由 schema 保证

        r = self.session.post(
            self.SUBMIT_URL,
            headers=self._headers(resource_id, request_id),
            json=body,
            timeout=self.timeout_s,
        )

        # Volcengine returns status in headers (body can be empty)
        status_code = r.headers.get("X-Api-Status-Code")
        message = r.headers.get("X-Api-Message")

        # HTTP 层错误优先报
        if r.status_code >= 400:
            raise RuntimeError(
                f"Submit HTTP failed: http={r.status_code}, "
                f"X-Api-Status-Code={status_code}, X-Api-Message={message}, "
                f"body={r.text[:300]}"
            )

        # 业务状态码缺失：不要默默放行（否则你排查会疯）
        if status_code is None:
            raise RuntimeError(
                f"Submit returned no X-Api-Status-Code header: http={r.status_code}, body={r.text[:300]}"
            )

        ok = {"20000000", "20000001", "20000002", "20000003"}
        if status_code not in ok:
            raise RuntimeError(
                f"Submit failed: X-Api-Status-Code={status_code}, X-Api-Message={message}, "
                f"http={r.status_code}, body={r.text[:300]}"
            )

        return request_id

    def query(self, request_id: str, resource_id: str) -> Dict[str, Any]:
        r = self.session.post(
            self.QUERY_URL,
            headers=self._headers(resource_id, request_id),
            json={},
            timeout=self.timeout_s,
        )
        
        # 检查 HTTP 状态码
        if r.status_code >= 400:
            raise RuntimeError(f"Query HTTP failed: http={r.status_code}, body={r.text[:300]}")
        
        # 检查豆包 API 状态码（通过 header 返回）
        status_code = r.headers.get("X-Api-Status-Code")
        message = r.headers.get("X-Api-Message", "")
        
        # 如果状态码表示错误（不是成功状态码），立即抛出异常
        if status_code and status_code not in ("20000000", "20000001", "20000002", "20000003", None):
            raise RuntimeError(
                f"Query failed: X-Api-Status-Code={status_code}, X-Api-Message={message}, "
                f"http={r.status_code}, body={r.text[:300]}"
            )
        
        try:
            return r.json()
        except Exception as e:
            raise RuntimeError(f"Query returned non-JSON: {e}; body={r.text[:300]}")

    def submit_and_poll(
            self,
            req: DoubaoASRRequest,
            *,
            resource_id: str,
            poll_interval_s: float = 2.0,
            max_wait_s: int = 3600,
    ) -> Dict[str, Any]:
        """
        Returns the final JSON response from /query.
        """
        # 添加日志输出
        from dubora.utils.logger import info
        
        info(f"提交任务到豆包 API...")
        req_id = self.submit(req, resource_id=resource_id)
        info(f"任务已提交，request_id: {req_id}")
        info(f"开始轮询查询结果（每 {poll_interval_s} 秒查询一次，最长等待 {max_wait_s} 秒）...")

        deadline = time.time() + max_wait_s
        last_json: Optional[Dict[str, Any]] = None
        poll_count = 0

        while time.time() < deadline:
            poll_count += 1
            info(f"第 {poll_count} 次查询任务状态...")
            
            try:
                j = self.query(req_id, resource_id)
            except RuntimeError as e:
                # 查询失败，立即抛出错误
                info(f"查询任务失败: {e}")
                raise
            
            last_json = j

            # 检查任务是否完成或失败
            result = j.get("result")
            
            # 优先检查是否有 utterances（表示任务完成）
            if isinstance(result, dict) and result.get("utterances"):
                info(f"任务完成！共查询 {poll_count} 次")
                return j
            
            # Some versions return a list at top-level "result"
            if isinstance(result, list) and len(result) > 0:
                for item in result:
                    if isinstance(item, dict) and item.get("utterances"):
                        info(f"任务完成！共查询 {poll_count} 次")
                        return j
            
            # 检查任务是否失败（只检查明确的错误状态）
            # 正常状态：processing, pending, success, completed, done
            # 错误状态：failed, error, timeout, cancelled 等
            error_statuses = ("failed", "error", "timeout", "cancelled", "rejected")
            
            if isinstance(result, dict):
                result_status = result.get("status", "").lower() if result.get("status") else ""
                if result_status in error_statuses:
                    error_msg = result.get("message") or result.get("error") or f"任务状态: {result_status}"
                    raise RuntimeError(f"任务失败: {error_msg}, 响应: {j}")
            
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        item_status = item.get("status", "").lower() if item.get("status") else ""
                        if item_status in error_statuses:
                            error_msg = item.get("message") or item.get("error") or f"任务状态: {item_status}"
                            raise RuntimeError(f"任务失败: {error_msg}, 响应: {j}")
            
            # 检查顶层状态字段
            top_status = (j.get("status") or "").lower() if j.get("status") else ""
            if top_status in error_statuses:
                error_msg = j.get("message") or j.get("error") or f"任务状态: {top_status}"
                raise RuntimeError(f"任务失败: {error_msg}, 响应: {j}")
            
            # 输出当前状态（如果有）
            status_display = j.get("status") or (result.get("status") if isinstance(result, dict) else None)
            if status_display:
                info(f"任务状态: {status_display}")

            time.sleep(poll_interval_s)

        raise TimeoutError(
            f"ASR polling timed out after {max_wait_s}s. Last response keys={list((last_json or {}).keys())}")


def guess_audio_format(url_or_path: str) -> str:
    """从 URL 或路径猜测音频格式"""
    lower = url_or_path.lower()
    # 映射到 AudioFormat enum 支持的值
    format_map = {
        "mp3": "mp3",
        "wav": "wav",
        "m4a": "m4a",
        "aac": "aac",
        "ogg": "ogg",
        "opus": "ogg",  # opus 通常封装在 ogg 容器中
        "flac": "wav",  # flac 映射到 wav（如果 API 不支持 flac）
    }
    for ext, format_val in format_map.items():
        if lower.endswith("." + ext):
            return format_val
    # Default to wav if unknown（更稳定）
    return "wav"
