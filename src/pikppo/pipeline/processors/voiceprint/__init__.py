"""
Voiceprint Processor 模块

公共 API：
- run_voiceprint(): 声纹识别全流程（需要 torchaudio）

注意：run_voiceprint 使用延迟导入，避免在不需要声纹功能时
触发 torchaudio 的加载。TTS 阶段只需要 speaker_to_role 子模块，
不需要 torchaudio。
"""


def run_voiceprint(*args, **kwargs):
    """延迟导入 processor 模块，避免触发 torchaudio 依赖。"""
    from .processor import run_voiceprint as _run
    return _run(*args, **kwargs)


__all__ = ["run_voiceprint"]
