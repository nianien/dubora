# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Dubora is a video dubbing pipeline that localizes Chinese short dramas into English-dubbed versions. It produces English audio dubbing with multi-character voice synthesis, hardburned English subtitles, and preserved background music.

## Monorepo Structure

The project is organized as 3 packages:

```
dubora/
├── packages/
│   ├── core/        → dubora-core     (data access layer: config, DbStore, utils, events)
│   ├── pipeline/    → dubora-pipeline  (execution layer: phases, processors, models, schema, types)
│   └── web/         → dubora-web       (API layer: FastAPI REST + Worker API)
├── web/             → React frontend
├── deploy/          → Dockerfiles + docker-compose + deploy scripts
├── sql/             → schema.sql, seed.sql (reference)
├── docs/            → DESIGN.md, IDE-GUIDE.md, CHANGELOG.md, GCP-DEPLOY.md
├── test/
├── Makefile
└── pyproject.toml   → root (dev tooling only)
```

## Common Commands

```bash
# Install
make install-core       # dubora-core only
make install-pipeline   # core + pipeline
make install-web        # core + web
make install-all        # all three packages
make install-dev        # dev tools (pytest, ruff)

# Development
make test               # pytest test/ -v
make lint               # ruff check packages/
make clean              # Remove __pycache__, .pytest_cache, etc.

# CLI
vsd-pipeline run 家里家外 5 --to burn       # Submit pipeline tasks to DB
vsd-pipeline worker                         # Long-running task executor
vsd-pipeline phases                         # List all phases
vsd-web serve --port 8765                   # Web server

# Docker
cd deploy && docker-compose up              # web + worker services
```

## Architecture

### 7-Phase Pipeline with Gates and Stages

```
Stage:  提取      识别              翻译              配音        合成
Phase:  extract → asr → parse  →  translate  →  tts → mix  →  burn
Gate:                        ↑              ↑
                      source_review   translation_review
```

| Phase | What it does | Technology |
|-------|-------------|------------|
| extract | Extract audio + separate vocals/accompaniment | FFmpeg + Demucs v4 |
| asr | Dual-source ASR: Doubao VAD (word-level) + Gemini (segmentation/speaker/emotion) | Doubao ASR + Gemini |
| parse | Gemini 骨架 + Doubao 文本 → LLM 校准 → end_ms 延长 → DB cues | Gemini LLM |
| translate | Incremental translation (utterance-level, per-cue writeback) | OpenAI / Gemini |
| tts | Incremental voice synthesis (voice_hash dirty check) + drift score check | VolcEngine seed-tts-1.0 |
| mix | Mix dubbed audio with accompaniment (adelay timeline) | FFmpeg |
| burn | Generate en.srt from DB cues + hardburn subtitles onto video | FFmpeg subtitles filter |

### Package Boundaries

- **dubora_core**: Config, utils, DbStore, EventEmitter, PipelineReactor, submit_pipeline, phase_registry (pure metadata), manifest, resources (emotions.json, voices.json), infra (tts_client). No heavy deps.
- **dubora_pipeline**: 7 Phase implementations, Processors, Models (LLM clients), PhaseRunner, PipelineWorker, RemoteStore, Schema, Types, DbManifest. Heavy deps (PyTorch, Demucs, etc.).
- **dubora_web**: FastAPI app factory, 11 REST routers (含 Worker API + Auth). Only depends on dubora_core.

### Key Import Paths

| Module | Package |
|---|---|
| `dubora_core.config` | core |
| `dubora_core.utils` | core |
| `dubora_core.store` (DbStore) | core |
| `dubora_core.events` | core |
| `dubora_core.submit` | core |
| `dubora_core.phase_registry` | core |
| `dubora_core.manifest` | core |
| `dubora_core.infra` | core |
| `dubora_pipeline.phases` | pipeline |
| `dubora_pipeline.processors` | pipeline |
| `dubora_pipeline.worker` | pipeline |
| `dubora_pipeline.runner` | pipeline |
| `dubora_pipeline.models` | pipeline |
| `dubora_pipeline.prompts` | pipeline |
| `dubora_pipeline.schema` | pipeline |
| `dubora_pipeline.types` | pipeline |
| `dubora_pipeline.phase` | pipeline |
| `dubora_pipeline.remote_store` (RemoteStore) | pipeline |
| `dubora_web` | web |

### DB-First Architecture (SQLite)

数据存储在 SQLite DB (`data/db/dubora.db`)，DB 是所有元数据的 SSOT。

核心表：**users** (用户) → **dramas** (剧集, user_id FK) → **episodes** → **cues** (原子段) / **utterances** (分组壳+TTS缓存) / **tasks** / **artifacts**

辅助表：**user_auths** (三方登录), **roles** (角色声线), **glossary** (术语表), **utterance_cues** (junction), **events** (审计日志)

- `dramas.user_id` NOT NULL，实现多账户数据隔离（子表通过 FK 链关联，无需冗余 user_id）
- `cues.speaker` 和 `utterances.speaker` 存 `roles.id` 整数（TEXT 列存整数字符串，应用层 `_cast_speaker()` 转 int）
- Dirty 判脏：`source_hash` (翻译) + `voice_hash` (TTS)

### Phase/Processor Separation Pattern

- **Phase** (`dubora_pipeline/phases/`): Orchestration layer — DB I/O, manifest updates, error handling.
- **Processor** (`dubora_pipeline/processors/`): Stateless business logic — pure computation.

### Task Execution Architecture

**Local mode** (single machine):
```
submit_pipeline()  → write first task to DB    (dubora_core.submit)
PipelineReactor    → on task_succeeded, next    (dubora_core.submit)
PipelineWorker     → poll DB, execute phases    (dubora_pipeline.worker)
```

**Remote mode** (web + worker on separate machines):
```
Worker (GPU)  ──  RemoteStore (HTTP)  ──→  Web (Worker API)  ──→  SQLite
              ──  /complete, /fail    ──→  PipelineReactor   ──→  next task
```

### Authentication & Multi-tenant

- Google OAuth 登录，支持 dev 模式（无 `GOOGLE_CLIENT_ID` 时自动 dev 登录）
- 用户数据写入 `users` + `user_auths` 表，cookie 存 `user_id`
- `AuthMiddleware` 在认证通过后注入 `request.state.user_id`
- 权限工具函数：`get_user_id()`, `require_drama_owner()`, `require_episode_owner()`（`_helpers.py`）
- 鉴权未启用时 user_id=None，所有隔离/校验逻辑跳过
- Worker API (`/api/worker/*`) 不加权限校验

## External Services

Configuration via `.env` file (loaded by python-dotenv):
- **Doubao ASR/TTS** (ByteDance/VolcEngine): `DOUBAO_APPID`, `DOUBAO_ACCESS_TOKEN`
- **VolcEngine TOS** (object storage, Doubao ASR 用): `TOS_*` env vars
- **Google Cloud Storage** (Gemini ASR 用): `GCS_*` env vars
- **OpenAI**: `OPENAI_API_KEY`
- **Google Gemini** (ASR + 校准 + 翻译): `GEMINI_API_KEY`
- **Google OAuth**: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SECRET_KEY`, `AUTH_ALLOWED_EMAILS`

## Iron Rules

- **禁止在 packages/ 下写任何兼容旧逻辑、迁移、适配代码。** 核心代码只面向当前 schema。迁移脚本放 `migrations/`。
- **禁止无效代码。** 不留 dead code、注释掉的旧逻辑、unused import、向后兼容 shim。
- **不要猜测路径/配置/表结构。** 先查实际文件确认。
- **改代码前必须读被调用函数的实现。** 不能只看函数名猜行为。改任何一行代码之前，先把它调用的函数点进去读一遍，理解它已经做了什么，再决定要不要加代码。默认做法是替换，不是叠加。
- **先查询，再验证，确认没问题再提供答案。** 不要凭记忆写代码。常量（模型名、preset名、环境变量名）必须从源代码复制，不可凭记忆编写。

## Key Paths & Config

### 数据目录总览

所有数据目录由 `DATA_DIR`（默认 `data/`）统一派生，`DB_DIR` 可独立覆盖。路径解析见 `dubora_core/config/settings.py`。

```
data/                                   # DATA_DIR (get_data_root())
├── db/dubora.db                        # DB_DIR (get_db_path())
├── pipeline/{drama}/{episode}/         # Phase 中间产物 (get_workdir())
│   ├── {ep}.wav                        # extract.audio
│   ├── {ep}-vocals.wav                 # extract.vocals
│   ├── {ep}-accompaniment.wav          # extract.accompaniment
│   ├── asr-doubao.json                 # asr.doubao (Doubao VAD 原始响应)
│   ├── asr-gemini.json                 # asr.gemini (Gemini ASR 结果)
│   ├── asr-calibrated.json             # LLM 校准中间结果（排查用）
│   ├── asr-result.json                 # 最终 cue rows（parse 产出）
│   ├── tts/segments/                   # tts.segments_dir (per-utterance WAV)
│   ├── {ep}-mix.wav                    # mix.audio
│   └── output/
│       ├── {ep}-zh.srt                 # subs.zh_srt
│       ├── {ep}-en.srt                 # subs.en_srt
│       └── {ep}-dubbed.mp4            # burn.video
├── gcs/                                # GCS 本地缓存 (get_gcs_store().cache_dir)
│   ├── dramas/{drama}/...             # 源视频、交付物
│   └── .sync/                         # SHA256 同步状态
├── tos/                                # TOS 本地缓存 (get_tos_store().cache_dir)
│   ├── dramas/{drama}/asr/{ep}.wav    # ASR 上传用音频
│   └── .sync/
└── .cache/
    ├── faststart/                      # MP4 remux 缓存 (media.py 用)
    └── voice-preview/                  # 声线预览缓存
```

**关键约束**：`data/pipeline/{drama}/` 下只有 episode 编号目录（1, 2, 3...），禁止创建其他子目录。

### FileStore 设计 (`dubora_core/utils/file_store.py`)

`RemoteFileStore` 用 `key`（字符串）统一本地缓存路径和远端 blob key：

```
key = "dramas/家里家外/dub/5-dubbed.mp4"
  → 本地: cache_dir / key = data/gcs/dramas/家里家外/dub/5-dubbed.mp4
  → 远端: gs://bucket/dramas/家里家外/dub/5-dubbed.mp4
```

两个实例：
- **GCS** (`get_gcs_store()`): cache_dir = `data/gcs/`，存源视频、交付物
- **TOS** (`get_tos_store()`): cache_dir = `data/tos/`，存 ASR 上传用音频

核心 API：

| 方法 | 行为 |
|------|------|
| `write_file(src, key)` | `shutil.copy2(src, cache_dir/key)` + 上传远端。**已含本地缓存写入，不需要手动 copy** |
| `write(key, data)` | 写 bytes 到本地 + 上传远端 |
| `upload(key)` | 仅上传（跳过已同步的，靠 `.sync` 文件判断） |
| `get(key)` | 返回本地绝对路径。本地缺失时从远端下载 |
| `get_url(key)` | 生成远端签名 URL |

同步机制：`.sync/{key}.sync` 文件记录 `local=<sha256> remote=<sha256>`，`upload()` 发现 local==remote 时跳过。

### Artifact 路径解析 (`dubora_core/manifest.py`)

`resolve_artifact_path(key, workspace)` 将 artifact key 映射为 workspace 内的确定性路径：

| Key | 路径（相对 workspace） |
|-----|----------------------|
| `extract.audio` | `{ep}.wav` |
| `extract.vocals` | `{ep}-vocals.wav` |
| `extract.accompaniment` | `{ep}-accompaniment.wav` |
| `asr.doubao` | `asr-doubao.json` |
| `asr.gemini` | `asr-gemini.json` |
| `subs.zh_srt` | `output/{ep}-zh.srt` |
| `subs.en_srt` | `output/{ep}-en.srt` |
| `tts.segments_dir` | `tts/segments` |
| `mix.audio` | `{ep}-mix.wav` |
| `burn.video` | `output/{ep}-dubbed.mp4` |

其中 `{ep}` = `workspace.name`（即 episode number）。路径不存 DB，运行时动态计算。

### Config 数据流

```
PipelineConfig (dataclass, settings.py)
  ↓ asdict()
worker.py: config_dict → RunContext.config
  ↓
Phase: ctx.config.get("key") 读全局配置
       ctx.config.get("phases", {}).get("phase_name", {}) 读 phase 级配置
```

注意：`worker.py:111` 设 `config_dict["phases"] = {}`，所以 phase 级配置始终为空。Phase 内需要用 `ctx.config.get("global_key")` 做 fallback。

### Phase 执行流程

```
PipelineWorker.tick()
  ├─ store.claim_any_pending_task()          # pending → running
  ├─ _get_workdir(drama, episode)            # data/pipeline/{drama}/{ep}/
  ├─ _resolve_video_path(episode)            # gcs.get(episode.path) → 本地路径
  ├─ PhaseRunner(manifest, workdir)
  │   ├─ should_run(phase)                   # 检查 version + 输入/输出文件
  │   ├─ resolve_inputs(phase)               # requires() → Artifact(relpath)
  │   ├─ allocate_outputs(phase)             # provides() → ResolvedOutputs(abs paths)
  │   └─ phase.run(ctx, inputs, outputs)     # 执行
  ├─ store.complete_task() / fail_task()
  └─ EventEmitter → PipelineReactor → 创建下一个 task
```

### Web 层文件服务

- **`/api/media/{key}`** (`media.py`): key = GCS blob key，通过 `gcs.get(key)` 解析到本地缓存。支持 Range header、自动 MP4 faststart remux。
- **`/api/export/{episode_id}/{filename}`** (`export.py`): 优先用 `resolve_artifact_path()` 找 workspace 本地文件，其次 `gcs.get(artifact.gcs_path)` 从 GCS 下载。

## Key Conventions

- Logs use no emoji (Chinese team preference, enforced in `utils/logger.py`)
- Config uses a custom `PipelineConfig` dataclass (`config/settings.py`), not stdlib
- Phase metadata is defined in `phase_registry.py` (pure data, no heavy imports)
- Phase implementations use lazy loading via `_LazyPhase` in `phases/__init__.py`
