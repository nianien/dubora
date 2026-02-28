"""
Pipeline phases registration.

使用延迟导入，避免未安装的可选依赖（如 torchaudio）阻塞整个 CLI。
Phase 类在真正执行 run() 时才 import 对应模块。
"""


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


# ── Gate 定义 ──────────────────────────────────────────────────
# 质量闸门：在指定 phase 完成后暂停，等待人工确认。
# Gate 不是 phase，不参与执行，只影响自动推进流程。

GATES = [
    {"key": "source_review",      "after": "reseg", "label": "校准"},
    {"key": "translation_review", "after": "align", "label": "审阅"},
]

# after_phase -> gate 的快速查找表
GATE_AFTER = {g["after"]: g for g in GATES}


# ── Stage 定义 ──────────────────────────────────────────────────
# 用户视角分组：5 个 Stage，每个包含 1~2 个 Phase。
# Stage 状态从子 phases 派生。

STAGES = [
    {"key": "extract",     "label": "提取", "phases": ["extract"]},
    {"key": "recognize",   "label": "识别", "phases": ["asr", "parse", "reseg"]},
    {"key": "translate",   "label": "翻译", "phases": ["mt", "align"]},
    {"key": "dub",         "label": "配音", "phases": ["tts", "mix"]},
    {"key": "compose",     "label": "合成", "phases": ["burn"]},
]


def build_phases(config=None) -> list:
    """
    根据 config 构建 phase 列表。

    config-sensitive 的依赖：
    - asr_use_vocals: True → ASR 输入为 extract.vocals，False → extract.audio
    """
    asr_use_vocals = getattr(config, "asr_use_vocals", False) if config else False
    asr_input = "extract.vocals" if asr_use_vocals else "extract.audio"

    return [
        _LazyPhase(
            "dubora.pipeline.phases.extract", "ExtractPhase",
            name="extract", version="1.0.0",
            requires=[], provides=["extract.audio", "extract.vocals", "extract.accompaniment"],
            label="提取",
        ),
        _LazyPhase(
            "dubora.pipeline.phases.asr", "ASRPhase",
            name="asr", version="1.0.0",
            requires=[asr_input], provides=["asr.asr_result"],
            label="语音识别",
        ),
        _LazyPhase(
            "dubora.pipeline.phases.parse", "ParsePhase",
            name="parse", version="1.1.0",
            requires=["asr.asr_result"], provides=["dub.dub_manifest"],
            label="生成字幕",
        ),
        _LazyPhase(
            "dubora.pipeline.phases.reseg", "ResegPhase",
            name="reseg", version="1.0.0",
            requires=["dub.dub_manifest", "asr.asr_result"],
            provides=["dub.dub_manifest"],
            label="断句优化",
        ),
        # ← Gate: source_review (校准)
        _LazyPhase(
            "dubora.pipeline.phases.mt", "MTPhase",
            name="mt", version="1.1.0",
            requires=["dub.dub_manifest"], provides=["mt.mt_input", "mt.mt_output"],
            label="翻译",
        ),
        _LazyPhase(
            "dubora.pipeline.phases.align", "AlignPhase",
            name="align", version="1.1.0",
            requires=["mt.mt_output", "extract.audio"],
            provides=["subs.en_srt", "dub.dub_manifest"],
            label="对齐",
        ),
        # ← Gate: translation_review (审阅)
        _LazyPhase(
            "dubora.pipeline.phases.tts", "TTSPhase",
            name="tts", version="1.1.0",
            requires=["dub.dub_manifest"],
            provides=["tts.segments_dir", "tts.segments_index", "tts.report", "tts.voice_assignment"],
            label="语音合成",
        ),
        _LazyPhase(
            "dubora.pipeline.phases.mix", "MixPhase",
            name="mix", version="2.1.0",
            requires=["dub.dub_manifest", "tts.segments_dir", "tts.report"],
            provides=["mix.audio"],
            label="混音",
        ),
        _LazyPhase(
            "dubora.pipeline.phases.burn", "BurnPhase",
            name="burn", version="1.0.0",
            requires=["mix.audio", "subs.en_srt"],
            provides=["burn.video"],
            label="烧字幕",
        ),
    ]


# 默认 phase 列表（兼容不传 config 的调用方）
ALL_PHASES = build_phases()
