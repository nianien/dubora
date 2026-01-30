"""
Subtitle Processor 模块（唯一公共入口）

架构原则：
- Subtitle Model 是 SSOT（唯一事实源）
- asr_post.py 是唯一可以生成 Subtitle Model 的模块
- 任何字幕文件（SRT/VTT）均为 Subtitle Model 的派生视图

公共 API：
- run(): 唯一对外入口（从 utterances 生成 Subtitle Model）

内部模块（不直接导入）：
- processor.py: Phase 层接口，调用 asr_post 生成 Subtitle Model
- impl.py: 核心业务逻辑封装
- asr_post.py: ASR raw → Subtitle Model（唯一生成点）
- render_srt.py: Subtitle Model → SRT 文件（格式渲染）
- profiles.py: 策略配置
- srt.py: SRT 格式处理（编解码，Segment ↔ SrtCue）
- subtitles.py: 向后兼容接口（包含文件 IO，不推荐使用）

数据流：
    ASR raw → asr_post.py → Subtitle Model (Segment[])
                                         ↓
                              render_srt.py → SRT 文件
                              render_vtt.py → VTT 文件（可选）
                              render_tts.py → TTS job（可选）
"""
from .processor import run

__all__ = ["run"]
