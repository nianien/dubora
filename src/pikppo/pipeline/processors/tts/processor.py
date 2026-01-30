"""
TTS Processor: 语音合成（唯一对外入口）

职责：
- 接收 Phase 层的输入（segments）
- 分配声线并合成语音
- 返回 ProcessorResult（不负责文件 IO）

架构原则：
- processor.py 是唯一对外接口
- 内部实现放在 impl.py, assign_voices.py, azure.py
- Phase 层只调用 processor.run()

注意：
- 当前实现仍包含文件 IO（向后兼容）
- 后续需要重构以完全分离文件 IO
"""
from typing import Any, Dict, List, Optional

from .._types import ProcessorResult
from .assign_voices import assign_voices
from .azure import synthesize_tts


def run(
    segments: List[Dict[str, Any]],
    *,
    reference_audio_path: Optional[str] = None,
    voice_pool_path: Optional[str] = None,
    azure_key: str,
    azure_region: str,
    language: str = "en-US",
    max_workers: int = 4,
    temp_dir: str,
) -> ProcessorResult:
    """
    分配声线并合成语音。
    
    Args:
        segments: segments 列表
        reference_audio_path: 参考音频路径（可选）
        voice_pool_path: voice pool JSON 文件路径（可选）
        azure_key: Azure TTS key
        azure_region: Azure region
        language: 语言代码
        max_workers: 最大并发数
        temp_dir: 临时目录（用于文件操作，后续应移除）
    
    Returns:
        ProcessorResult:
        - data.voice_assignment: speaker -> voice_id 映射
        - data.audio_path: 合成的音频文件路径（临时，后续应返回音频数据）
        - meta: 元数据
    """
    # TODO: 重构以完全分离文件 IO
    # 当前实现仍使用文件路径，后续应改为返回音频数据
    
    # 1. 分配声线（需要临时文件，后续应改为纯内存操作）
    import tempfile
    import json
    from pathlib import Path
    
    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)
    
    segments_file = temp_path / "segments.json"
    with open(segments_file, "w", encoding="utf-8") as f:
        json.dump(segments, f, indent=2, ensure_ascii=False)
    
    voice_assignment_path = assign_voices(
        str(segments_file),
        reference_audio_path,
        voice_pool_path,
        str(temp_path),
    )
    
    # 读取 voice assignment
    with open(voice_assignment_path, "r", encoding="utf-8") as f:
        voice_assignment = json.load(f)
    
    # 2. TTS 合成（需要临时文件，后续应改为纯内存操作）
    audio_path = synthesize_tts(
        str(segments_file),
        voice_assignment_path,
        voice_pool_path,
        str(temp_path),
        azure_key=azure_key,
        azure_region=azure_region,
        language=language,
        max_workers=max_workers,
    )
    
    return ProcessorResult(
        outputs=[],  # 由 Phase 声明 outputs，processor 只负责业务处理
        data={
            "voice_assignment": voice_assignment,
            "audio_path": audio_path,  # 临时：后续应返回音频数据
        },
        metrics={
            "segments_count": len(segments),
            "speakers_count": len(set(seg.get("speaker", "speaker_0") for seg in segments)),
        },
    )
