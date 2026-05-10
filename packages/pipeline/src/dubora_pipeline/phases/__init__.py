"""
Pipeline phases registration.

使用延迟导入，避免未安装的可选依赖（如 torchaudio）阻塞整个 CLI。
Phase 类在真正执行 run() 时才 import 对应模块。
"""
from dubora_core.phase_registry import (
    GATES,
    GATE_AFTER,
    PHASE_META,
    PHASE_NAMES,
    STAGES,
)


class _LazyPhase:
    """延迟加载的 Phase 代理。

    name/version/requires/provides 静态声明在此，不触发模块 import。
    只有调用 run() 等业务方法时才加载真正的 Phase 类。
    """

    def __init__(self, module_path: str, class_name: str,
                 name: str, version: str,
                 requires: list, provides: list, *,
                 label: str = ""):
        self._module_path = module_path
        self._class_name = class_name
        self._instance = None
        # 静态元数据，不触发 import
        self.name = name
        self.version = version
        self._requires = requires
        self._provides = provides
        self.label = label

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
    - asr_use_vocals: True → ASR 输入为 extract.vocals，False → extract.audio
    - asr_primary: 主 ASR 模型，决定 asr/parse 的 artifact 边界
    """
    asr_use_vocals = getattr(config, "asr_use_vocals", False) if config else False
    asr_input = "extract.vocals" if asr_use_vocals else "extract.audio"
    asr_primary = getattr(config, "asr_primary", "doubao") if config else "doubao"
    primary_artifact = f"asr.{asr_primary}"

    return [
        _LazyPhase(
            "dubora_pipeline.phases.extract", "ExtractPhase",
            name="extract", version="1.0.0",
            requires=[], provides=["extract.audio", "extract.vocals", "extract.accompaniment"],
            label="提取",
        ),
        _LazyPhase(
            "dubora_pipeline.phases.asr", "ASRPhase",
            name="asr", version="4.1.0",
            requires=[asr_input], provides=[primary_artifact],
            label="语音识别",
        ),
        _LazyPhase(
            "dubora_pipeline.phases.parse", "ParsePhase",
            name="parse", version="4.1.0",
            requires=[primary_artifact], provides=[],
            label="生成字幕",
        ),
        # ← Gate: source_review (校准)
        _LazyPhase(
            "dubora_pipeline.phases.translate", "TranslatePhase",
            name="translate", version="4.0.0",
            requires=["extract.audio"],
            provides=[],
            label="翻译",
        ),
        # ← Gate: translation_review (审阅)
        _LazyPhase(
            "dubora_pipeline.phases.tts", "TTSPhase",
            name="tts", version="2.0.0",
            requires=["extract.audio"],
            provides=["tts.segments_dir"],
            label="语音合成",
        ),
        _LazyPhase(
            "dubora_pipeline.phases.mix", "MixPhase",
            name="mix", version="3.0.0",
            requires=["extract.audio", "tts.segments_dir"],
            provides=["mix.audio"],
            label="混音",
        ),
        _LazyPhase(
            "dubora_pipeline.phases.burn", "BurnPhase",
            name="burn", version="2.0.0",
            requires=["mix.audio"],
            provides=["burn.video"],
            label="烧字幕",
        ),
    ]


# 默认 phase 列表（兼容不传 config 的调用方）
ALL_PHASES = build_phases()
