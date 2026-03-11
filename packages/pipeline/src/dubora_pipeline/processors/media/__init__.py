"""
Media Processor 模块（唯一公共入口）

公共 API：
- run(): 唯一对外入口（从视频提取音频）

内部模块（不直接导入）：
- impl.py: 内部实现
"""
from .processor import run

__all__ = ["run"]
