"""
Pipeline phases registration.

使用延迟导入，避免未安装的可选依赖（如 torchaudio）阻塞整个 CLI。
Phase 类在真正执行 run() 时才 import 对应模块。
"""
from typing import Optional


class _LazyPhase:
    """延迟加载的 Phase 代理。

    name/version/requires/provides 静态声明在此，不触发模块 import。
    只有调用 run() 等业务方法时才加载真正的 Phase 类。
    """

    def __init__(self, module_path: str, class_name: str,
                 name: str, version: str,
                 requires: list, provides: list):
        self._module_path = module_path
        self._class_name = class_name
        self._instance = None
        # 静态元数据，不触发 import
        self.name = name
        self.version = version
        self._requires = requires
        self._provides = provides

    def _load(self):
        if self._instance is None:
            import importlib
            mod = importlib.import_module(self._module_path)
            cls = getattr(mod, self._class_name)
            self._instance = cls()
        return self._instance

    def requires(self):
        return self._requires

    def provides(self):
        return self._provides

    def run(self, ctx, inputs, outputs):
        return self._load().run(ctx, inputs, outputs)

    def __repr__(self):
        return f"<Phase {self.name} v{self.version}>"


def build_phases(config=None) -> list:
    """
    根据 config 构建 phase 列表。

    config-sensitive 的依赖：
    - asr_use_vocals: True → ASR 输入为 sep.vocals，False → demux.audio
    """
    asr_use_vocals = getattr(config, "asr_use_vocals", False) if config else False
    asr_input = "sep.vocals" if asr_use_vocals else "demux.audio"

    return [
        _LazyPhase(
            "dubora.pipeline.phases.demux", "DemuxPhase",
            name="demux", version="1.0.0",
            requires=[], provides=["demux.audio"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.sep", "SepPhase",
            name="sep", version="1.0.0",
            requires=["demux.audio"], provides=["sep.vocals", "sep.accompaniment"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.asr", "ASRPhase",
            name="asr", version="1.0.0",
            requires=[asr_input], provides=["asr.asr_result"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.sub", "SubtitlePhase",
            name="sub", version="1.0.0",
            requires=["asr.asr_result"], provides=["subs.subtitle_model", "subs.zh_srt"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.mt", "MTPhase",
            name="mt", version="1.0.0",
            requires=["subs.subtitle_model", "asr.asr_result"], provides=["mt.mt_input", "mt.mt_output"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.align", "AlignPhase",
            name="align", version="1.0.0",
            requires=["subs.subtitle_model", "mt.mt_output", "demux.audio"],
            provides=["subs.subtitle_align", "subs.en_srt", "dub.dub_manifest"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.tts", "TTSPhase",
            name="tts", version="1.0.0",
            requires=["dub.dub_manifest"],
            provides=["tts.segments_dir", "tts.segments_index", "tts.report", "tts.voice_assignment"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.mix", "MixPhase",
            name="mix", version="1.0.0",
            requires=["dub.dub_manifest", "tts.segments_dir", "tts.report"],
            provides=["mix.audio"],
        ),
        _LazyPhase(
            "dubora.pipeline.phases.burn", "BurnPhase",
            name="burn", version="1.0.0",
            requires=["mix.audio", "subs.en_srt"],
            provides=["burn.video"],
        ),
    ]


# 默认 phase 列表（兼容不传 config 的调用方）
ALL_PHASES = build_phases()
