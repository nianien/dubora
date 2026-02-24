"""
Machine Translation (MT) module for video remix pipeline.

公共 API：
- run(): 唯一对外入口（翻译整集 segments）

内部模块（不直接导入）：
- impl.py: 内部实现
- openai_translate.py: 向后兼容接口（不导出）
"""
from .processor import run

__all__ = ["run"]
