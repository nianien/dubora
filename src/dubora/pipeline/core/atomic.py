"""
原子写入工具：避免中途中断留下半文件
"""
import shutil
import tempfile
from pathlib import Path
from typing import Optional


def atomic_write(
    content: bytes | str,
    target_path: Path,
    *,
    encoding: Optional[str] = "utf-8",
) -> None:
    """
    原子写入文件（先写临时文件，再 rename）。
    
    Args:
        content: 文件内容（bytes 或 str）
        target_path: 目标路径
        encoding: 文本编码（仅当 content 是 str 时使用）
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 创建临时文件（在同一目录，确保 rename 是原子操作）
    temp_path = target_path.parent / f".{target_path.name}.tmp"
    
    try:
        if isinstance(content, str):
            temp_path.write_text(content, encoding=encoding)
        else:
            temp_path.write_bytes(content)
        
        # 原子 rename
        temp_path.replace(target_path)
    except Exception:
        # 清理临时文件
        if temp_path.exists():
            temp_path.unlink()
        raise


def atomic_copy(
    source_path: Path,
    target_path: Path,
) -> None:
    """
    原子复制文件（先复制到临时文件，再 rename）。
    
    Args:
        source_path: 源文件路径
        target_path: 目标文件路径
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 创建临时文件
    temp_path = target_path.parent / f".{target_path.name}.tmp"
    
    try:
        shutil.copy2(source_path, temp_path)
        # 原子 rename
        temp_path.replace(target_path)
    except Exception:
        # 清理临时文件
        if temp_path.exists():
            temp_path.unlink()
        raise
