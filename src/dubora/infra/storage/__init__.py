"""
Infrastructure Storage 模块

职责：
- 外部存储系统适配器（TOS / S3 / GCS / OSS）
- 文件上传 / 下载 / 预签名 URL 生成

边界：
- 只负责存储 IO，不涉及业务逻辑
- 不解析字幕、不处理 ASR、不接触模型
- 不处理 URL/Path 判断（由 pipeline 层处理）
"""
from .tos import TosStorage, TosConfig, load_tos_config

__all__ = [
    "TosStorage",
    "TosConfig",
    "load_tos_config",
]
