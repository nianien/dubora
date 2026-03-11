"""
Phase metadata registry: pure data, no heavy imports.

This module is the single source of truth for phase names, gates, and stages.
It can be imported by both web (lightweight) and pipeline (heavy) packages
without triggering lazy-loaded phase implementations.
"""

PHASE_NAMES: list[str] = [
    "extract", "asr", "parse", "translate", "tts", "mix", "burn",
]

PHASE_META: list[dict] = [
    {"name": "extract",   "label": "提取",     "version": "1.0.0"},
    {"name": "asr",       "label": "语音识别", "version": "1.0.0"},
    {"name": "parse",     "label": "生成字幕", "version": "2.0.0"},
    {"name": "translate", "label": "翻译",     "version": "4.0.0"},
    {"name": "tts",       "label": "语音合成", "version": "2.0.0"},
    {"name": "mix",       "label": "混音",     "version": "3.0.0"},
    {"name": "burn",      "label": "烧字幕",   "version": "2.0.0"},
]

# Quality gates: pause after a phase for human review.
GATES: list[dict] = [
    {"key": "source_review",      "after": "parse",     "label": "校准"},
    {"key": "translation_review", "after": "translate",  "label": "审阅"},
]

# after_phase -> gate quick lookup
GATE_AFTER: dict[str, dict] = {g["after"]: g for g in GATES}

# User-facing stage grouping (5 stages, each with 1-2 phases).
STAGES: list[dict] = [
    {"key": "extract",   "label": "提取", "phases": ["extract"]},
    {"key": "recognize", "label": "识别", "phases": ["asr", "parse"]},
    {"key": "translate", "label": "翻译", "phases": ["translate"]},
    {"key": "dub",       "label": "配音", "phases": ["tts", "mix"]},
    {"key": "compose",   "label": "合成", "phases": ["burn"]},
]
