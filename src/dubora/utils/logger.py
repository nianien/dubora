"""
统一的日志工具模块

提供统一的日志接口，替代直接使用 print。
支持不同级别的日志输出，不包含 emoji。
"""
import sys
from typing import Optional


class Logger:
    """简单的日志记录器，不依赖 logging 模块"""
    
    def __init__(self, prefix: str = ""):
        self.prefix = prefix
    
    def _format(self, level: str, message: str) -> str:
        """格式化日志消息"""
        if self.prefix:
            return f"[{level}] {self.prefix}: {message}"
        return f"[{level}] {message}"
    
    def info(self, message: str):
        """信息级别日志"""
        print(self._format("INFO", message), file=sys.stdout)
    
    def success(self, message: str):
        """成功级别日志"""
        print(self._format("SUCCESS", message), file=sys.stdout)
    
    def warning(self, message: str):
        """警告级别日志"""
        print(self._format("WARN", message), file=sys.stderr)
    
    def error(self, message: str):
        """错误级别日志"""
        print(self._format("ERROR", message), file=sys.stderr)
    
    def debug(self, message: str):
        """调试级别日志"""
        print(self._format("DEBUG", message), file=sys.stdout)


# 全局默认日志记录器
_default_logger = Logger()


def info(message: str):
    """信息级别日志"""
    _default_logger.info(message)


def success(message: str):
    """成功级别日志"""
    _default_logger.success(message)


def warning(message: str):
    """警告级别日志"""
    _default_logger.warning(message)


def error(message: str):
    """错误级别日志"""
    _default_logger.error(message)


def debug(message: str):
    """调试级别日志"""
    _default_logger.debug(message)


def get_logger(prefix: str = "") -> Logger:
    """获取带前缀的日志记录器"""
    return Logger(prefix=prefix)
