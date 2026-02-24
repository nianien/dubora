"""dubora.storage.tos

TOS（火山引擎对象存储）存储适配器。

职责：
- 本地文件 -> TOS（基于内容哈希去重）
- 生成预签名 URL（GET）

说明：
- 这个模块只做“存储”一件事。
- 依赖官方 `tos` SDK（TosClientV2）。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dubora.utils.logger import info


@dataclass(frozen=True)
class TosConfig:
    """TOS 配置（从环境变量加载）。"""

    access_key_id: str
    secret_access_key: str
    region: str
    bucket: str
    endpoint: str  # e.g. "tos-cn-beijing.volces.com" (no scheme)


def load_tos_config() -> TosConfig:
    """从环境变量读取 TOS 配置。"""

    access_key_id = os.getenv("TOS_ACCESS_KEY_ID")
    secret_access_key = os.getenv("TOS_SECRET_ACCESS_KEY")
    region = os.getenv("TOS_REGION", "cn-beijing")
    bucket = os.getenv("TOS_BUCKET", "pikppo-video")
    endpoint = os.getenv("TOS_ENDPOINT", f"tos-cn-{region}.volces.com")

    if not access_key_id:
        raise ValueError("TOS_ACCESS_KEY_ID 环境变量未设置")
    if not secret_access_key:
        raise ValueError("TOS_SECRET_ACCESS_KEY 环境变量未设置")

    endpoint = endpoint.replace("https://", "").replace("http://", "")
    return TosConfig(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
        bucket=bucket,
        endpoint=endpoint,
    )


def _sha256_file(path: Path) -> str:
    """计算文件内容 SHA256（用于去重）。"""

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_object_key(local_path: Path, content_hash: str, *, prefix: Optional[str] = None, n: int = 8) -> str:
    """
    根据本地文件路径和内容哈希生成 TOS object_key。
    
    规则：{prefix or parent_dir}/{stem}-{hash[:n]}{suffix}
    例如：
        - prefix="dbqsfy", audio/1.wav -> dbqsfy/1-abc12345.wav
        - prefix=None, videos/dbqsfy/1.wav -> dbqsfy/1-abc12345.wav
        - prefix=None, 1.wav -> files/1-abc12345.wav (无目录时使用默认目录)
    """
    # 使用提供的 prefix，或从路径提取父目录名
    if prefix:
        parent_dir = prefix
    else:
        parent_dir = local_path.parent.name if local_path.parent.name else "files"
    
    stem = local_path.stem
    suffix = local_path.suffix
    return f"{parent_dir}/{stem}-{content_hash[:n]}{suffix}"


class TosStorage:
    """最小可用的 TOS 存储封装。"""

    def __init__(self, config: Optional[TosConfig] = None):
        self.config = config or load_tos_config()

        try:
            import tos  # type: ignore
        except ImportError as e:
            raise RuntimeError("缺少依赖：pip install tos") from e

        self._tos = tos
        self.client = tos.TosClientV2(
            self.config.access_key_id,
            self.config.secret_access_key,
            self.config.endpoint,
            self.config.region,
        )

    def exists(self, object_key: str) -> bool:
        """检查对象是否存在。404 返回 False，其他错误直接抛出。"""
        try:
            self.client.head_object(self.config.bucket, object_key)
            return True
        except self._tos.exceptions.TosServerError as e:  # type: ignore[attr-defined]
            if e.status_code == 404:
                return False
            # 其他错误（403/401/500等）直接抛出，不吞异常
            raise RuntimeError(
                f"TOS head_object failed: {e.status_code} {getattr(e, 'message', '')}"
            ) from e
        except Exception as e:
            # 网络错误等其他异常，直接抛出
            raise RuntimeError(f"TOS 检查对象存在性时发生异常: {e}") from e

    def upload_file(self, local_path: Path, object_key: str) -> None:
        """上传文件到 TOS。统一使用 upload_file（SDK 自动处理大小）。"""
        size = local_path.stat().st_size
        if size <= 0:
            raise RuntimeError(f"无法上传空文件: {local_path}")

        # 统一使用 upload_file（SDK 自动处理分块，避免小文件全读内存）
        self.client.upload_file(self.config.bucket, object_key, str(local_path))

    def presigned_get(self, object_key: str, *, expires_seconds: int = 36000) -> str:
        out = self.client.pre_signed_url(
            self._tos.enum.HttpMethodType.Http_Method_Get,
            self.config.bucket,
            object_key,
            expires=expires_seconds,
        )
        return out.signed_url

    def upload(
        self,
        local_path: Path,
        *,
        prefix: Optional[str] = None,
        overwrite: bool = False,
        expires_seconds: int = 36000,
    ) -> str:
        """
        上传本地文件到 TOS，返回预签名 GET URL。
        
        object_key 由 storage 内部根据文件路径和内容哈希自动生成。
        格式：{prefix or parent_dir}/{stem}-{hash[:8]}{suffix}
        例如：
            - prefix="dbqsfy", audio/1.wav -> dbqsfy/1-abc12345.wav
            - prefix=None, abc/1.wav -> abc/1-abc12345.wav
        
        Args:
            local_path: 本地文件路径
            prefix: 可选的目录前缀（如果提供，将覆盖从路径提取的目录名）
            overwrite: 是否强制覆盖（默认 False，幂等上传）
                - False: 若对象已存在，直接返回 URL，不重新上传
                - True: 强制重新上传，覆盖远端对象
            expires_seconds: 预签名 URL 有效期（秒，默认 10 小时）
        
        Returns:
            预签名 GET URL
        
        Raises:
            FileNotFoundError: 如果本地文件不存在
        """
        local_path = local_path.expanduser().resolve()
        if not local_path.exists():
            raise FileNotFoundError(f"文件不存在: {local_path}")

        # 计算内容哈希并生成 object_key
        content_hash = _sha256_file(local_path)
        object_key = _build_object_key(local_path, content_hash, prefix=prefix)

        # overwrite=False: 检查是否存在，存在则跳过上传
        if not overwrite and self.exists(object_key):
            info(f"TOS 对象已存在，跳过上传: {object_key}")
            return self.presigned_get(object_key, expires_seconds=expires_seconds)

        # overwrite=True 或对象不存在: 执行上传
        info(f"TOS upload: {local_path.name} -> {object_key}")
        self.upload_file(local_path, object_key)

        return self.presigned_get(object_key, expires_seconds=expires_seconds)
