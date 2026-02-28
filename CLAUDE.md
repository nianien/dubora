# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dubora is a video dubbing pipeline that localizes Chinese short dramas into English-dubbed versions. It produces English audio dubbing with multi-character voice synthesis, hardburned English subtitles, and preserved background music.

## Common Commands

```bash
# Installation (role-oriented, no "install-all" by design)
make install          # Core dependencies only
make install-dev      # + pytest, black, ruff
make install-dub      # + Demucs, Azure/Google TTS, OpenAI
make install-full     # All optional dependencies (recommended for local dev)

# Development
make test             # pytest tests/ -v
make lint             # ruff check src/ tools/
make clean            # Remove __pycache__, .pytest_cache, etc.

# CLI (two aliases: vsd and vr)
vsd run video.mp4 --to burn                # Full pipeline
vsd run video.mp4 --to asr                 # Run up to ASR phase only
vsd run video.mp4 --from mt --to tts       # Force re-run from MT through TTS
vsd bless video.mp4 parse                  # Accept manual edits to parse phase outputs
vsd phases                                 # List all phases
```

## Architecture

### 8-Phase Pipeline with Gates and Stages

```
Stage:  提取      识别          [校准]  翻译          [审阅]  配音        合成
Phase:  extract   asr → parse          mt → align            tts → mix   burn
Gate:                           ↑                      ↑
                        source_review          translation_review
```

| Phase | What it does | Technology |
|-------|-------------|------------|
| extract | Extract audio + separate vocals/accompaniment | FFmpeg + Demucs v4 |
| asr | Speech recognition with speaker diarization | Doubao ASR (ByteDance) |
| parse | Generate dub manifest from ASR output | Local post-processing |
| mt | Translate Chinese → English | OpenAI GPT-4o or Google Gemini |
| align | Timeline alignment + re-segmentation | Local alignment |
| tts | Voice synthesis per segment | VolcEngine seed-tts-1.0 |
| mix | Mix dubbed audio with accompaniment | FFmpeg adelay + amix |
| burn | Hardburn English subtitles onto video | FFmpeg subtitles filter |

### Phase/Processor Separation Pattern

- **Phase** (`pipeline/phases/`): Orchestration layer — file I/O, manifest updates, error handling. Implements the `Phase` abstract base class.
- **Processor** (`pipeline/processors/`): Stateless business logic — pure computation, no file I/O orchestration. Testable in isolation.

### Three SSOT Data Models (`schema/`)

| Model | File | Produced by | Consumed by |
|-------|------|------------|-------------|
| ASR Result | `asr-result.json` | asr phase | parse phase |
| Subtitle Model v1.3 | `subtitle.model.json` | parse phase | mt, align phases |
| Dub Manifest | `dub.model.json` | align phase | tts, mix phases |

### Pipeline Framework (`pipeline/core/`)

- **Manifest** (`manifest.py`): JSON state machine tracking phase status and artifact fingerprints. Persisted at `{workdir}/manifest.json`.
- **PhaseRunner** (`runner.py`): Orchestrates execution with a 7-level incremental decision tree (force flag → manifest lookup → version bump → input fingerprint → config fingerprint → output fingerprint → status check).
- **Fingerprints** (`fingerprints.py`): SHA256 hashing of files and canonicalized JSON for incremental execution. `vsd bless` refreshes fingerprints after manual edits.
- **Atomic** (`atomic.py`): Write-to-temp-then-rename pattern for safe file operations.

### Voice Mapping (Single File)

```
roles.json
roles:         { "PingAn": "en_male_...", } # role_id → voice_type
default_roles: { "male": "LrNan1" }         # fallback by gender
```

Located at `{drama_dir}/dub/dict/roles.json`. Human-filled for role assignment.

### Workspace Layout

Videos are organized as `{path}/{drama}/dub/{episode}/` with subdirectories organized by asset lifecycle:
- `input/` — immutable after creation (extracted audio, vocals, accompaniment, ASR result)
- `state/` — SSOT, human-editable (dub.json, subtitle.model.json)
- `derived/` — recomputable intermediates (mt/, tts/, voiceprint/, mixed audio)
- `output/` — final deliverables (dubbed video, SRT subtitles)
- `.cache/` — internal optimization cache (hidden)

## External Services

Configuration via `.env` file (loaded by python-dotenv):
- **Doubao ASR/TTS** (ByteDance/VolcEngine): `DOUBAO_APPID`, `DOUBAO_ACCESS_TOKEN`
- **VolcEngine TOS** (object storage for ASR uploads): `TOS_*` env vars
- **OpenAI**: `OPENAI_API_KEY`
- **Google Gemini**: `GEMINI_API_KEY`

## Key Conventions

- Logs use no emoji (Chinese team preference, enforced in `utils/logger.py`)
- Config uses a custom `PipelineConfig` dataclass (`config/settings.py`), not stdlib
- Phase ordering is defined in `pipeline/phases/__init__.py` via `ALL_PHASES`
- The project has no `install-all` target intentionally — different environments need different dependency sets
