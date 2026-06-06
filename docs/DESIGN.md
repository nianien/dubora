# Dubora Architecture Document

> 国产短剧英文配音流水线完整架构参考。基于 2026-03 DB-First 架构。

## 1. 项目定位

将中文短剧（竖屏 9:16，单集 2-5 分钟）自动转化为英文配音版本。

**输入**：单集 mp4 视频（无剧本、无角色表）

**输出**：
- 英文配音成片（多角色声线、保留 BGM）
- 英文字幕（硬烧到视频）

**设计原则**：
- 效果优先：宁可慢，也要质量稳定
- 在线服务为主：ASR/MT/TTS 全在线，仅人声分离在本地
- 声线池模式：不做原演员克隆，用预定义声线池区分角色
- DB-First：SQLite 是所有元数据的 SSOT，支持增量重跑和前端实时编辑

---

## 2. 系统架构

### 2.1 Pipeline 总览

```
Stage:  提取      识别              翻译              配音        合成
Phase:  extract → asr → parse  →  translate  →  tts → mix  →  burn
Gate:                        ↑              ↑
                      source_review   translation_review
```

7 个 Phase + 2 个 Gate，5 个 Stage。数据存储在 SQLite DB (`data/db/dubora.db`)，task 队列驱动异步执行。

| Phase | 职责 | 技术 |
|-------|------|------|
| extract | 提取音频 + 人声/伴奏分离 | FFmpeg + Demucs v4 |
| asr | Doubao Seed-ASR 2.0 单源 + Gemini 多模态分析输出场景上下文（注入 corpus.context dialog_ctx） | Doubao ASR + Gemini scene context |
| parse | Doubao utterances → emotion 回填 → end_ms 延长 → DB cues | — |
| translate | 增量翻译 (utterance 级, per-cue 回填) | OpenAI / Gemini |
| tts | 语音合成 (增量, voice_hash 判脏) + drift_score 检查 | VolcEngine seed-tts-1.0 |
| mix | 混音 (adelay timeline placement) | FFmpeg |
| burn | 从 DB cues 生成 en.srt + 烧字幕到视频 | FFmpeg subtitles filter |

**数据流**：

```
extract → audio.wav, vocals.wav, accompaniment.wav (文件)
asr     → asr-doubao.json (Doubao Seed-ASR 2.0) + asr-context.json (Gemini 场景上下文)
parse   → asr-calibrated.json (LLM 校准中间结果) → asr-result.json → DB cues 表
  ── [source_review gate: 人工在 IDE 中校准] ──
translate → DB cues.text_en (翻译回填) + utterances (分组 + TTS 缓存)
  ── [translation_review gate: 人工审阅翻译] ──
tts     → tts/segments/ (逐句音频) + DB utterances 更新
mix     → {ep}-mix.wav (混音)
burn    → output/en.srt (从 DB cues 生成) + output/dubbed.mp4 (成片)
```

### 2.2 Task 执行架构

支持两种部署模式：

**本地模式**（单机，web + worker 共享 SQLite）：
```
submit_pipeline()  → 写第一个 task 到 DB，退出
PipelineReactor    → 监听 task_succeeded 事件，创建下一个 task
PipelineWorker     → 全局 worker，轮询 DB 取 pending task，执行
```

**远程模式**（双机，task 通过 HTTP API 访问 DB）：
```
task（GPU 机器）                       web（常驻机器）
┌──────────────────┐                 ┌──────────────────────┐
│ PipelineWorker   │  ── HTTP ──→    │ Worker API (FastAPI)  │
│   PhaseRunner    │                 │   PipelineReactor     │
│   RemoteStore ───┤                 │   DbStore ────────────┤→ SQLite
└──────────────────┘                 └───────────────────────┘
```

远程模式下：
- Worker 通过 `RemoteStore`（HTTP 代理）访问数据，接口与 `DbStore` 相同
- Reactor 调度逻辑集中在 web 侧（`/complete` 和 `/fail` 端点内部运行）
- Worker 只做：领任务 → 执行 → 报告结果
- Phase 代码无需感知本地/远程差异

Worker.tick() 流程：
1. `claim_any_pending_task()` — 原子地把 pending → running
2. 构建 RunContext (workdir, config, store, episode_id)
3. PhaseRunner.run_phase() 执行
4. 成功 → complete_task + emit task_succeeded → Reactor 创建下一个 task
5. 失败 → fail_task + emit task_failed → Reactor 设 episode status=failed

### 2.3 Gate 机制

Gate 在指定 phase 完成后暂停，等待人工确认。

```python
GATES = [
    {"key": "source_review",      "after": "parse",     "label": "校准"},
    {"key": "translation_review", "after": "translate",  "label": "审阅"},
]
```

- parse 完成后 → 创建 source_review gate task (status=pending) → episode status=review
- 用户通过 Web UI 确认 → `pass_gate_task()` → gate task status=succeeded → Reactor 继续
- translate 完成后 → 同理 translation_review

Gate 作为 task 存储在 tasks 表 (type=gate key)。

### 2.4 Monorepo 分包

```
dubora/
├── packages/
│   ├── core/        → dubora-core     (数据访问层)
│   ├── pipeline/    → dubora-pipeline (执行层)
│   └── web/         → dubora-web      (API 层)
├── web/             → React 前端
├── deploy/          → Dockerfile + docker-compose + deploy 脚本
├── sql/             → schema.sql, seed.sql（参考）
├── docs/            → 文档
└── test/
```

**包职责**：

| 包 | 职责 | 重型依赖 |
|---|------|---------|
| **dubora_core** | Config, DbStore, EventEmitter, PipelineReactor, submit_pipeline, phase_registry, resources, utils (logger, file_store), infra (tts_client) | 无 |
| **dubora_pipeline** | 7 Phase 实现, Processors, Models (LLM clients), PhaseRunner, PipelineWorker, RemoteStore, Schema, 类型定义 | PyTorch, Demucs, etc. |
| **dubora_web** | FastAPI app factory, 11 REST routers (含 Worker API + Auth) | 无 |

**设计原则**：
- **core 是纯数据访问层**：只做 DB CRUD、配置、事件，不含任何执行逻辑
- **pipeline 是执行层**：Phase/Processor/Schema/Types 全部在此，web 不依赖 pipeline
- **web 是 API 层**：只依赖 core，通过 Worker API 为远程 worker 提供数据访问

- **Phase**：编排层，实现 `Phase` 抽象（`requires` / `provides` / `run`），负责 DB 读写、调用 Processor、返回 `PhaseResult`。
- **Processor**：无状态业务逻辑，只做计算，不直接依赖 DB 或 workspace 路径约定，便于单测与替换。
- **Worker**：轮询 DB task 队列，claim → execute → complete/fail。Reactor 监听事件，自动创建下一个 task。

Phase 通过 `_LazyPhase` 延迟加载，避免在不需要的阶段导入重型依赖（如 torchaudio）。DB-only phases (parse, translate) 的 `provides()` 返回 `[]`，不产生文件 artifact。

### 2.5 CLI 用法

Pipeline CLI（`vsd-pipeline`）和 Web CLI（`vsd-web`）分离：

```bash
# Pipeline 命令
vsd-pipeline run 家里家外 5 --to burn               # 提交 pipeline tasks 到 DB（本地模式）
vsd-pipeline run 家里家外 5 --from translate --to tts  # 从指定阶段强制重跑
vsd-pipeline run 家里家外 4-70 --to burn             # 批量提交
vsd-pipeline run 家里家外 5 --to burn --api-url http://web:8765  # 远程模式：通过 Worker API 提交
vsd-pipeline worker                                  # 启动独立 worker 进程（本地模式）
vsd-pipeline worker --api-url http://web:8765        # 启动远程 worker（通过 HTTP API 访问 DB）
vsd-pipeline phases                                  # 列出所有阶段

# Web 命令
vsd-web serve --port 8765                            # 启动 Web 服务器
```

### 2.6 文件布局

所有数据由 `DATA_DIR`（默认 `data/`）统一管理，`DB_DIR` 可独立覆盖。

```
data/                                    # 数据根 (env: DATA_DIR)
+-- db/
|   +-- dubora.db                       # SQLite DB (env: DB_DIR, 默认 DATA_DIR/db)
|
+-- pipeline/{剧名}/{集号}/               # 集级 workspace (workdir)
|   +-- {集号}.wav                      #   提取的音频
|   +-- {集号}-vocals.wav               #   人声
|   +-- {集号}-accompaniment.wav        #   伴奏
|   +-- asr-doubao.json                 #   Doubao VAD 原始响应
|   +-- asr-context.json                #   Gemini 业务场景上下文 (豆包 corpus.context)
|   +-- asr-calibrated.json             #   LLM 校准中间结果（排查用）
|   +-- asr-result.json                 #   最终 cue rows（parse 产出）
|   +-- voice-assignment.json           #   声线分配快照
|   +-- {集号}-mix.wav                  #   最终混音
|   +-- tts/                            #   TTS 产物
|   |   +-- segments/                   #     逐句 TTS 音频
|   |   +-- report.json                 #     TTS 报告
|   |   +-- segments.json               #     段索引
|   +-- output/                         #   最终交付物（GCS 暂存）
|   |   +-- {集号}-dubbed.mp4           #     成片
|   |   +-- {集号}-en.srt               #     英文字幕
|   |   +-- {集号}-zh.srt               #     中文字幕
|   +-- .cache/tts/                     #   TTS 临时文件（用完即删）
|
+-- gcs/                                # GCS 缓存
+-- tos/                                # TOS 缓存
+-- .cache/
    +-- faststart/                      # MP4 faststart remux 缓存
    +-- voice-preview/                  # 声线试听缓存
```

过程文件直接放 workdir 根目录，只保留 `tts/`（segments 多文件）和 `output/`（GCS 交付物暂存）两个子目录。Web 和 Pipeline 部署时共享同一 `DATA_DIR` volume。

### 2.7 认证与多账户

**认证流程（Google OAuth）**：
1. 前端跳转 `/api/auth/google/login` → Google OAuth → `/api/auth/google/callback`
2. Callback 获取 email/name/picture → `upsert_user()` → `upsert_user_auth()` → 写 signed cookie (`user_id`)
3. Dev 模式（无 `GOOGLE_CLIENT_ID`）：自动创建 dev@localhost 用户

**Middleware 注入**：
- `AuthMiddleware`：认证通过后 `request.state.user_id = session["user_id"]`
- 跳过路径：`/api/auth/*`, `/api/health`, `/api/worker/*`

**数据隔离**：
- `dramas.user_id NOT NULL` — 每个 drama 属于一个用户
- `UNIQUE(user_id, name)` — 不同用户可创建同名 drama
- 子表（episodes, cues 等）通过 FK 链关联 drama，权限校验走 `require_drama_owner()` / `require_episode_owner()`
- 鉴权未启用时 user_id=None，所有隔离/校验逻辑跳过

**环境变量**：

| 变量 | 说明 |
|------|------|
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID（空则 dev 模式） |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `AUTH_SECRET_KEY` | Cookie 签名密钥（默认 `dubora-dev-key`） |
| `AUTH_ALLOWED_EMAILS` | 允许登录的邮箱白名单，逗号分隔，支持通配符（如 `*@company.com`） |

---

## 3. 表结构

### 3.1 核心表关系

```
users (1) ──── (N) user_auths
  │
  └── (N) dramas (1) ──┬── (N) episodes (1) ──┬── (N) cues
                       │                      ├── (N) utterances
                       │                      ├── (N) tasks
                       │                      ├── (N) events (via tasks)
                       │                      └── (N) artifacts
                       ├── (N) roles
                       └── (N) glossary
                                   utterance_cues (junction: utterance ↔ cues)
```

- `dramas.user_id` NOT NULL，实现多账户数据隔离
- 子表（episodes, cues, utterances 等）通过 FK 链关联 drama，无需冗余 user_id

### 3.2 cues 表 — 原子段

```sql
CREATE TABLE cues (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id   INTEGER NOT NULL REFERENCES episodes(id),
    text         TEXT NOT NULL DEFAULT '',        -- 中文原文
    text_en      TEXT NOT NULL DEFAULT '',        -- 英文翻译
    start_ms     INTEGER NOT NULL,
    end_ms       INTEGER NOT NULL,
    speaker      TEXT NOT NULL DEFAULT '',        -- ASR 原始 label（"0", "1"...），永不被覆写
    role_id      INTEGER REFERENCES roles(id),    -- 用户人工指定的语义角色，可空
    emotion      TEXT NOT NULL DEFAULT 'neutral',
    gender       TEXT,
    kind         TEXT NOT NULL DEFAULT 'speech',  -- 'speech' 或 'sing'
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

**字段约定（speaker / role_id 双字段设计）：**

- `text`: 中文原文，来自 ASR，用户可编辑。
- `text_en`: 英文翻译，由 translate phase 回填到 cue 上。burn phase 直接读 cues.text_en 生成 en.srt。
- `speaker`: **ASR 物理声源 label**，parse phase 写入后永不被覆写。代表"谁在发声"。
- `role_id`: **用户语义角色绑定**，可空。代表"这段话属于哪个剧情角色"。

**第一性原理**：声音合成的物理依据是 speaker（声源），role 是 speaker 的可选语义分组（"男主"/"旁白"）。两者关系不是 1:1：一个 role 可关联多个 speaker（童年/成年男主），一个 speaker 也可能在不同 cue 上对应不同 role。

**音色决策（TTS 阶段，无 fallback）**：
```
直接用 utt.role_id 查 roles[utt.role_id].voice_type / sample_audio
任何 utt.role_id 为 NULL 或 role.voice_type 为空 → TTS phase 入口 fail-fast
```

`role_id` 必须由用户在字幕编辑页显式指定，详见 §3.5。

**diff_and_save 机制（三类改动各自的 gate / phase 重排）：**
```
用户编辑 cue → diff_and_save() 分三类改动检测：
  _SOURCE_FIELDS (text/start_ms/end_ms/emotion/kind) 或 新增/删除 cue
    → reset_to_gate('source_review')  回到「校准」
  role_id 变（只影响 TTS 音色）
    → reset_to_phase('tts')  不动 gate，仅重排 TTS 及下游
  text_en 变
    → reset_to_gate('translation_review')  回到「审阅」
```

`source_hash` 不含 role_id（翻译不参考音色）；`voice_hash` 含 role_id（音色决定 TTS）。改 role_id 不会触发重翻，只让 utt 进入 TTS dirty 集合，按 voice_hash 增量合成。

注意：`speaker` 字段不在 `_SOURCE_FIELDS` 里（用户改不动它），`role_id` 取代了原来 `speaker` 在该列表的位置。

### 3.3 utterances 表 — 分组壳 + TTS 缓存

```sql
CREATE TABLE utterances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id      INTEGER NOT NULL REFERENCES episodes(id),
    text_cn         TEXT NOT NULL DEFAULT '',    -- 冗余缓存：合并自 sub-cues
    text_en         TEXT NOT NULL DEFAULT '',    -- 冗余缓存：合并自 sub-cues text_en
    speaker         TEXT NOT NULL DEFAULT '',    -- 冗余缓存：来自 group[0].speaker (ASR label)
    role_id         INTEGER REFERENCES roles(id),-- 冗余缓存：来自 group[0].role_id
    emotion         TEXT NOT NULL DEFAULT 'neutral',
    gender          TEXT,
    kind            TEXT NOT NULL DEFAULT 'speech',
    tts_policy      TEXT,                        -- JSON: {max_rate, allow_extend_ms}
    source_hash     TEXT,                        -- 翻译判脏用
    voice_hash      TEXT,                        -- TTS 判脏用
    audio_path      TEXT,                        -- TTS 输出路径
    tts_duration_ms INTEGER,
    tts_rate        REAL,
    tts_error       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

**关键字段语义：**
- `text_cn`, `text_en`: 冗余缓存，不是 SSOT。真值在 cues 表。由 `sync_utterance_text_cache()` 同步。
- `speaker`, `role_id`: 冗余缓存，每次 `calculate_utterances` 时从 `group[0]` 覆盖写入。SSOT 在 cues 表。前端不直接编辑 utterance 的 speaker/role_id。
- `source_hash`: 翻译判脏。SHA256(合并 sub-cue text)[:16]。translate phase 翻译成功后写入。
- `voice_hash`: TTS 判脏。SHA256(text_en|role_id|emotion)[:16]。TTS phase 合成成功后写入。**改用 role_id 而非 speaker**（音色由 role 决定）。
- `tts_policy`: JSON 字符串 `{"max_rate": 1.3, "allow_extend_ms": 500}`。由 translate phase 根据 utterance 间隙计算。
- `start_ms`, `end_ms`: **不存储在表中**，由 `get_utterances()` 从 junction 关联的 cues 实时计算。

**合并不变量**：同一个 utterance 的所有 cue 必须满足 §4.4 的身份判定（role 优先于 speaker）。

### 3.4 utterance_cues 表 — 关联表

```sql
CREATE TABLE utterance_cues (
    utterance_id INTEGER NOT NULL REFERENCES utterances(id),
    cue_id       INTEGER NOT NULL REFERENCES cues(id),
    PRIMARY KEY (utterance_id, cue_id)
);
```

由 `calculate_utterances()` 管理。utterance 本身不存 start_ms/end_ms，从关联的 cues 实时计算。

### 3.5 roles 表 — 角色声线映射

```sql
CREATE TABLE roles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id      INTEGER NOT NULL REFERENCES dramas(id),
    name          TEXT NOT NULL,         -- 角色名（由用户在「角色管理」创建/编辑）
    voice_type    TEXT NOT NULL DEFAULT '',     -- volcengine: 预设声线 ID; fish: 平台 reference_id
    sample_audio  TEXT NOT NULL DEFAULT '',     -- fish 克隆参考音频 (GCS key)
    role_type     TEXT NOT NULL DEFAULT 'extra', -- 'lead' / 'supporting' / 'extra' / 'narrator'
    UNIQUE(drama_id, name)
);
```

**音色挂载位置**：voice_type / sample_audio 挂在 role 上（不挂在 speaker 上）。这是实用简化——同 role 多 speaker 时音色统一，符合典型短剧场景。

**无默认 fallback：未指定角色 / 未设置音色一律 fail-fast**

历史版本曾有"默认 role 兜底"逻辑（utt.role_id 为空回退到 `name="默认"` 的 role），2026-06 已彻底移除。当前 TTS phase 入口对未分配的 utterance 直接 fail-fast 报错，强制用户显式分配角色和音色，避免"悄悄用了别人的音色"这类隐式错误。

**TTS 音色解析完整链路：**

```
ASR 输出 speaker="0","1"
→ parse phase: 写 cue.speaker = "0"/"1"，cue.role_id = NULL
→ 用户在字幕编辑页选择性给 cue 标 role：cue.role_id = 10057 ("男主")
→ 用户在「角色管理」给 role 设置 voice_type
→ TTS phase 入口 fail-fast 检查：
    任何 utt.role_id 为 NULL → 报错"存在 N 条字幕未指定角色…"
    role.voice_type 为空     → 报错"角色【X】未设置音色…"
→ 检查通过 → voice_assignment["speakers"][str(utt.role_id)] = {voice_type/sample_audio}
→ volcengine.py / fish.py: speakers.get(str(utt.role_id)) → 命中音色
```

**Fish 模式 sample_audio 自动截取（详见 §4.6）**：从 `cue.role_id == role.id` 关联的 cue 里挑最优一条，从 vocals 截 ~5s。drama 级共享，跨集复用。

### 3.6 其他表

| 表 | 用途 |
|---|------|
| users | 用户 (email UNIQUE, name, picture) |
| user_auths | 三方登录 (user_id, provider, provider_id, UNIQUE(provider, provider_id)) |
| dramas | 剧集 (name, user_id NOT NULL, synopsis, UNIQUE(user_id, name)) |
| episodes | 集数 (drama_id, number INTEGER, path, status) |
| tasks | 任务队列 (type=phase name 或 gate key, status=pending/running/succeeded/failed) |
| events | 审计日志 (task 生命周期事件) |
| artifacts | episode 级文件注册表 (kind, gcs_path, checksum) |
| glossary | 术语表 (drama_id, type=name/slang, src, target) |

---

## 4. 各阶段实现

### 4.1 Extract（音频提取 + 人声分离）

| | |
|---|---|
| **输入** | 原视频 mp4 |
| **输出** | `extract.audio` (WAV 16k mono), `extract.vocals` (人声), `extract.accompaniment` (伴奏) |
| **实现** | FFmpeg（提取）+ Demucs htdemucs v4（分离，进程内 Python API） |

Demucs 是 pipeline 中最慢的环节（2 分钟音频需 3-10 分钟 CPU），但显著提升 ASR 准确率和混音质量。

**分离为何不走 `demucs` 命令行**：demucs CLI 保存 wav 时调用 `torchaudio.save`，而 torchaudio 2.9 的 `save`/`load` 只能用 torchcodec 编解码（旧的 soundfile/sox backend 已移除）。torchcodec 在 macOS 上常因 rpath 找不到匹配版本的 ffmpeg 动态库（libavutil 等）而 dlopen 失败（torchcodec issue #570）。因此分离改为**进程内调用 demucs 的 Python API**（`get_model` + `apply_model`），输出用 `soundfile`（wheel 自带 libsndfile，不依赖系统 ffmpeg）写 wav，彻底绕开 torchcodec。读取输入仍用 demucs 的 `AudioFile`（走 ffmpeg 命令行）。模型按名 `lru_cache`，worker 串行处理多集时复用。实现见 `processors/sep/impl.py`。

### 4.2 ASR（单源语音识别 + 场景上下文）

| | |
|---|---|
| **输入** | `extract.audio`（可配置为 `extract.vocals`） |
| **输出** | `asr.doubao` (Doubao 原始响应), `asr-context.json` (Gemini 业务场景上下文) |
| **服务** | Doubao Seed-ASR 2.0（唯一 ASR 引擎）+ Gemini 多模态场景分析（仅生成上下文，不参与识别） |
| **预设** | Doubao: `asr_spk_semantic`；Gemini: `gemini_model`（默认 gemini-3.5-flash） |

**单源 + 场景上下文增强**：

1. **Gemini 听音频 → 业务场景描述**（200 字内）
   - 识别视频类型（短剧 / 广告 / Vlog / ASMR / 带货 / 教学 / 纪录片）
   - 对白格式特点（清单式 / 对话式 / 念稿式 / 旁白）
   - 关键名词列表（人名、品牌名、产品名、地名、术语）
   - 缓存到 `workspace/asr-context.json`，避免重复调用

2. **场景描述注入豆包 corpus.context dialog_ctx**
   - `CorpusConfig.from_scene()` 拼出官方文档要求的 jsonstring 格式
   - 豆包内部按场景偏向词选择候选，对领域特定音频提升显著

3. **豆包 Seed-ASR 2.0**（resource_id `volc.seedasr.auc`）
   - preset `asr_spk_semantic`：不走 VAD，让模型语义切分
   - 移除 `ssd_version`：1.0 时代字段，在 2.0 上会触发 ~32s 处终止识别 bug
   - 热词从 DB glossary 表 (type='name') 加载

**实测准确率（12 星座+零食创意广告）**：
- 单跑豆包默认配置：~50%（且 32s 处截断）
- 单跑 Gemini ASR：~50%（且有幻觉风险）
- **豆包 2.0 + Gemini scene context：~92%，全集 60 秒完整覆盖**

> **注**：曾有 doubao + tencent + fish + LLM 对齐的多源融合方案，2026-06 下线。
> Gemini scene context 让单源豆包效果已超越当时的融合方案，复杂度大幅下降。

### 4.3 Parse（豆包 utterances → DB cues）

| | |
|---|---|
| **输入** | `asr.doubao` (Doubao Seed-ASR 2.0 原始响应) |
| **输出** | DB cues 表 + `asr-result.json` (最终 cue rows) |
| **核心逻辑** | `get_doubao_utterances` → emotion 回填 → end_ms 延长 → 写 DB |

**流程**：
1. 读取 `asr-doubao.json`，提取 utterances：`start_time`/`end_time` → `start_ms`/`end_ms`，`additions.emotion`/`additions.gender`/`additions.speaker` 一同带出
2. emotion 回填（`fill_null_emotions`）：从相邻同 speaker 段继承
3. end_ms 延长（`extend_end_ms`）：保证字幕最小显示时长
4. 清空旧 cues + utterances，全量写入新 cues（**`cue.speaker` 直接存 ASR 原始 label，`cue.role_id` 默认 NULL**）

Parse 完成后进入 `source_review` 门控，等待人工在 IDE 中校准。校准时用户必须给每条 cue 指定 `role_id`，否则后续 TTS phase 会 fail-fast 报错（无默认 role 兜底）。

### 4.4 calculate_utterances — Greedy Merge

由 `store.calculate_utterances()` 执行（在 translate phase 和 diff_and_save 中调用）。

```
cues (按 start_ms 排序) → 贪心合并:
  same_identity + 同 emotion + gap ≤ 500ms + 总时长 ≤ 10000ms → 合入同组
  否则 → 新组
```

**身份判定（role 优先于 speaker）**：

```python
if c1.role_id is not None and c2.role_id is not None:
    same_identity = c1.role_id == c2.role_id          # 都有 role → 看 role
elif c1.role_id is None and c2.role_id is None:
    same_identity = c1.speaker == c2.speaker          # 都没 role → 看 ASR speaker
else:
    same_identity = False                              # 一有一无 → 不合并
```

对照表：

| c1.role_id | c2.role_id | c1.speaker | c2.speaker | 能合并？ | 依据 |
|---|---|---|---|---|---|
| NULL | NULL | "0" | "0" | ✓ | speaker 一致 |
| NULL | NULL | "0" | "1" | ✗ | speaker 不同 |
| 10057 | 10057 | "0" | "0" | ✓ | role 一致 |
| 10057 | 10057 | "0" | "1" | ✓ | role 一致（修正 ASR 错分） |
| 10057 | 10058 | "0" | "0" | ✗ | role 不同 |
| NULL | 10057 | "0" | "0" | ✗ | 一有一无 |

**副作用**：用户给两个 ASR 错分的 cue 标了同一个 role 后，下次 calculate_utterances 会把它们合并——这是修正 ASR 错分的天然路径。

**匹配机制**：每组算 cue_id 集合 (frozenset)，与 DB 现有 utterance 的 cue_id 集合对比：

- 匹配 → 保留 (TTS 缓存复用)
- 不匹配 → 新建 utterance (source_hash=NULL → 标记为脏 → 触发翻译)
- 多余 → 删除

关键设计：**用 cue_id 集合匹配**，不用 source_hash。这保证了 TTS 缓存的精确复用。

### 4.5 Translate（增量翻译）

| | |
|---|---|
| **输入** | DB cues (text), `extract.audio` (for duration probe) |
| **输出** | DB cues.text_en (翻译回填), utterances (分组 + TTS 缓存) |
| **服务** | Google Gemini 2.0 Flash / OpenAI GPT-4o-mini |

**流程**：
1. `calculate_utterances()`: 贪心合并 cues → utterances + junction
2. `get_dirty_utterances_for_translate()`: 找脏行 (source_hash 不匹配或 NULL)
3. 对每个脏 utterance:
   - 单 cue → 直接翻译
   - 多 cue → 编号格式 `[1] text1\n[2] text2` 送 LLM，返回 per-cue 翻译
   - 翻译结果回填到 cue.text_en
4. 计算 tts_policy (根据 utterance 间隙)
5. 更新 utterance: text_en cache + source_hash + tts_policy

**Name Guard 机制**：
- 提取中文人名 → 替换为占位符 `<<NAME_1>>` → 翻译 → 还原为英文名
- 英文名从 DB dictionary 表 (type='name') 查找
- 缺失的名字通过 LLM 补全

Translate 完成后进入 `translation_review` 门控，等待人工审阅翻译质量。

### 4.6 TTS（增量语音合成 + Drift 检查）

| | |
|---|---|
| **输入** | DB utterances, `extract.audio` (for duration probe) |
| **输出** | `tts.segments_dir` (逐句 WAV 文件), DB utterances 更新 |
| **引擎** | VolcEngine seed-tts-1.0（预设声线）/ Fish Audio（声音克隆）。通过 `TTS_ENGINE` 环境变量切换 |

**音色决策树（fail-fast，无默认 fallback）**：

```
TTS phase 入口先做整体检查（dirty 集合之前）：
  collect 未指定角色 / 缺音色:
    no_role_count = count utt where utt.role_id is NULL
    missing_voice_roles = { role.name for role in used_roles if not role.voice_type }
  若有任何 → 返回 PhaseResult(failed, VoiceAssignmentError)，错误信息引导用户：
    "存在 N 条字幕未指定角色，请在字幕编辑页为相应字幕指定角色；
     角色【X】、【Y】未设置音色，请到「角色管理」页面完成设置。"

检查通过之后:
  对每条 utterance:
    role = roles[utt.role_id]
    if engine == "volcengine": use role.voice_type
    if engine == "fish":
      if role.sample_audio is empty:
        auto_extract_sample(role, ...)   # 见下方"自动截取规则"
      use role.sample_audio（或 role.voice_type 作 Fish 平台 reference_id）
```

**Fish 模式自动截取 sample 规则**：

```python
对 每个 role 缺 sample_audio 的:
  candidates = cues WHERE role_id == role.id AND kind='speech'
                     AND duration in [2s, 8s] AND len(text) >= 4
  best = sort by abs(duration - 5s)[0]
  ffmpeg 截 vocals.wav → data/pipeline/{drama}/roles/{role.id}_sample.wav (24kHz mono, ±100ms pad)
  → GCS upload dramas/{drama}/roles/{role.id}_sample.wav
  → 写回 role.sample_audio
```
drama 级共享，跨集复用；没有"默认 role 不限 role_id 兜底"的特殊路径。

**合成流程**：
1. 读所有 utterances，构建 full DubManifest（含 role_id 字段）
2. 入口 fail-fast 检查未指定 role / 缺 voice_type 的角色
3. `get_dirty_utterances_for_tts()`: 找脏行 (voice_hash 不匹配)
4. 无脏行 → no-op
5. Fish 模式：先跑 `_ensure_role_samples()` 给缺样本的 role 自动截取
6. 并行逐句合成（默认 4 workers）
7. 静音裁剪 + 语速调整（超 budget 加速到 max_rate 1.3x）
8. 更新 DB: audio_path, tts_duration_ms, tts_rate, voice_hash
9. Drift score 检查: tts_duration_ms / physical_ms > 1.1 则警告

**voice_assignment 构建（按 role_id 索引）：**
```python
# 对每个 role 构建 entry
voice_assignment["speakers"][str(role.id)] = {
    "voice_type": role.voice_type,
    "sample_audio_local": resolve_local_path(role.sample_audio),
}
# 引擎层直接用 utt.role_id 查找（fail-fast 已保证非空）:
voice_info = voice_assignment["speakers"].get(str(utt.role_id), {})
```

### 4.7 Mix（混音）

| | |
|---|---|
| **输入** | `extract.audio`, `tts.segments_dir`, DB utterances + cues |
| **输出** | `mix.audio` |
| **实现** | FFmpeg adelay + amix |

**Timeline-First 架构**：
- 用 FFmpeg `adelay` 滤镜将每段 TTS 精确放置到时间轴位置
- 伴奏轨 + TTS 轨混合，TTS 播放时伴奏自动压低（ducking，10:1 压缩比）
- Sing cues (kind='sing') 保留原始人声时间窗
- `apad + atrim` 强制输出与原音频等长
- 校验输出时长 (tolerance ±50ms)

### 4.8 Burn（生成 SRT + 字幕烧录）

| | |
|---|---|
| **输入** | `mix.audio`, DB cues (text_en) |
| **输出** | `burn.video` -> 最终成片 mp4, `output/en.srt` |
| **实现** | FFmpeg subtitles 滤镜硬烧 |

**流程**：
1. 从 DB cues.text_en 生成 en.srt (写到 output/en.srt)
2. mix.audio + en.srt → FFmpeg subtitles filter → dubbed video

---

## 5. Dirty 判脏机制

### 5.1 source_hash — 翻译判脏

```python
def _compute_source_hash(src_cues: list[dict]) -> str:
    """子 cue 内容指纹 (text + timing + role_id + emotion) 的 SHA256[:16]。"""
    parts: list[str] = []
    for c in src_cues:
        parts.append(c.get("text", ""))
        parts.append(str(c.get("start_ms", 0)))
        parts.append(str(c.get("end_ms", 0)))
        parts.append(str(c.get("role_id") or ""))   # role_id 取代原 speaker
        parts.append(c.get("emotion", "neutral"))
    return sha256("|".join(parts).encode()).hexdigest()[:16]
```

**触发翻译的条件 (get_dirty_utterances_for_translate)：**
1. `source_hash IS NULL` → 从未翻译 (新 utterance)
2. `source_hash != _compute_source_hash(当前 sub-cues)` → 内容变了
3. 任何 sub-cue 的 `text_en` 为空 → 翻译不完整

**source_hash 写入时机：** translate phase 翻译成功后。不在 calculate_utterances 中更新，保证新 utterance 的 source_hash=NULL 自动标记为脏。

注：`speaker` 字段（ASR label）不参与 source_hash——它是物理标识不影响翻译。

### 5.2 voice_hash — TTS 判脏

```python
def _compute_voice_hash(text_en: str, role_id: int | None, emotion: str = "") -> str:
    """text_en + effective_role_id + emotion 的 SHA256[:16]。"""
    effective = str(role_id) if role_id is not None else "_default"
    data = f"{text_en}|{effective}|{emotion}"
    return sha256(data.encode()).hexdigest()[:16]
```

**触发 TTS 的条件 (get_dirty_utterances_for_tts)：**
- `voice_hash != _compute_voice_hash(当前 text_en, role_id, emotion)`
- 即 text_en、role_id、emotion 任一变化 → 重新合成

注：决定音色的是 role（无 fallback），所以 voice_hash 用 role_id 而非 speaker。

**voice_hash 写入时机：** TTS phase 合成成功后。

### 5.3 diff_and_save — 前端编辑判脏

```python
_SOURCE_FIELDS = ("text", "start_ms", "end_ms", "emotion", "kind")
# diff_and_save 三类改动各自的重排策略：
#  1) source 字段变 / 新增 / 删除 cue → reset_to_gate('source_review')   回到「校准」
#  2) role_id 变（只影响 TTS 音色） → reset_to_phase('tts')              不动 gate
#  3) text_en 变                   → reset_to_gate('translation_review') 回到「审阅」
# 优先级 source > role > text_en（多类同时变取靠前的）
```

注：
- `speaker` 字段（ASR label）不在 `_SOURCE_FIELDS` 也不在任何改动集合里——前端不能修改它
- `role_id` **不在** `_SOURCE_FIELDS`：翻译不参考 role，只触发 TTS 重排（`reset_to_phase('tts')` 删 tts/mix/burn 已 succeeded 任务并新建 tts pending）
- source 字段变 → `calculate_utterances()` 可能生成新 utterance → source_hash=NULL → 触发重翻

### 5.4 sync_utterance_text_cache

用户在前端编辑 cue.text_en → `diff_and_save()` → `sync_utterance_text_cache()`:
- 重算 utterance 的 text_cn/text_en 缓存
- 如果 text_en 变了 → 更新 voice_hash → TTS 判脏

---

## 6. 前端显示规则

### 6.1 数据类型

```typescript
interface Cue {
  speaker: string             // ASR 物理 label（"0", "1"...），只读展示
  role_id: number | null      // 用户绑定的角色 FK，可空
  // ...
}

interface Role {
  id: number
  name: string
  voice_type: string
  sample_audio: string         // fish 克隆参考音频 GCS key
  role_type: string            // 'lead' | 'supporting' | 'extra' | 'narrator'
}
```

### 6.2 Speaker / Role 显示

**Badge 格式**：`{role_name or "未分配"}({speaker_label})`

```
00:12  [未分配(0)]      你好，请坐
00:14  [David(1)]       抱歉，路上堵车
```

- 着色按 `role_id` 分组（同 role 同色），role_id=NULL 统一灰色
- 点击 badge 弹下拉选 role → `updateCue(id, { role_id: role.id })`
- speaker label 显示但不可编辑（ASR 给的物理身份）
- 新建角色：先 `saveRoles()` 获取真实 id，再 `updateCue(id, { role_id: newId })`

### 6.3 Roles API

```
GET  /episodes/{drama}/roles  → {"roles": [{id, name, voice_type, sample_audio, role_type}, ...]}
PUT  /episodes/{drama}/roles  → body: {"roles": [{id?, name, voice_type, role_type?}, ...]}
     有 id → 更新, 无 id → 新建, 缺失 → 删除
```


### 6.4 快捷键 (useKeyboard)

- 1-9: 快速切换 role (按 roles 列表 index)
- Ctrl+B: 分割 cue
- Ctrl+M: 合并 cue
- Ctrl+I: 插入空 cue (默认 role_id: `refCue?.role_id ?? null`)
- Delete/Backspace: 删除 cue
- Alt+Arrow: 微调 cue 边界 ±50ms
- Shift+Alt+Arrow: 微调 cue 边界 ±200ms

### 6.5 Undo/Redo

- Command 模式: `{apply, inverse, description}`
- 所有 cue 操作通过 `useUndoableOps()` hook
- `changeRole(id, oldRoleId: number | null, newRoleId: number | null)`

### 6.6 Auto-save

- cue 修改后 2 秒自动保存 (`scheduleAutoSave`)
- 保存调用 `diff_and_save()` → cv bump → calculate_utterances → sync_utterance_text_cache

---

## 7. 外部服务依赖

| 服务 | 用途 | 环境变量 |
|------|------|---------|
| **豆包 ASR** | 中文语音识别 (VAD word-level) | `DOUBAO_APPID`, `DOUBAO_ACCESS_TOKEN` |
| **火山引擎 TOS** | 音频文件存储（Doubao ASR 用） | `TOS_ACCESS_KEY_ID`, `TOS_SECRET_ACCESS_KEY` |
| **Google Cloud Storage** | 音频文件存储（Gemini ASR 用） | `GCS_*` env vars |
| **火山引擎 TTS** | 英文语音合成 | 同豆包 credentials |
| **OpenAI** | 翻译（GPT-4o-mini）、重断句、情绪修正 | `OPENAI_API_KEY` |
| **Gemini** | ASR + 校准 + 翻译（Gemini 2.0 Flash） | `GEMINI_API_KEY` |
| **Demucs** | 人声分离 | 本地 |
| **FFmpeg** | 音频/视频处理 | 本地 |

---

## 8. ASR Calibration IDE

IDE 用于在 `source_review` 门控处人工校准 ASR 结果。详细操作手册见 [IDE-GUIDE.md](./IDE-GUIDE.md)。

**核心能力**：
- 可视化编辑 DB cues（文本、说话人、情绪、时间轴）
- 段落拆分/合并/插入/删除（支持撤销重做）
- 视频同步播放 + 字幕叠加
- 流水线运行/取消（PipelinePanel）
- 配音视频回放对比
- Voice Casting（声线分配，DB roles 表）

**启动**：
```bash
vsd-web serve --port 8765     # 启动 Web 服务器
```

---

## 9. 典型工作流

```bash
# 1. 首次全流程（本地模式）
vsd-pipeline run 家里家外 5 --to burn
#    pipeline 自动在 source_review 门控暂停

# 2. 启动 Web 服务器 + Worker
vsd-web serve --port 8765          # 终端 1
vsd-pipeline worker                # 终端 2（或远程 worker）

# 3. 人工校准（在浏览器中完成）
#    - 打开 http://localhost:8765
#    - 选择剧集 → 校准 speaker、文本、时间轴
#    - Cmd+S 保存 (自动保存到 DB)
#    - PipelinePanel 点击「继续」通过 source_review 门控
#    - 流水线自动从 translate 继续
#    - translation_review 门控暂停，审阅翻译
#    - 点击「继续」→ 流水线跑完 tts → mix → burn

# 4. 如果只改了翻译相关，从 translate 重跑
vsd-pipeline run 家里家外 5 --from translate --to burn

# 5. 批量处理
vsd-pipeline run 家里家外 1-79 --to burn

# 6. 远程模式（双机部署）
vsd-pipeline run 家里家外 5 --to burn --api-url http://web:8765
vsd-pipeline worker --api-url http://web:8765
```

---

## 10. 技术债

### 10.1 SQLite TEXT 列存 int 的类型不一致

- `cues.speaker` 和 `utterances.speaker` 列类型为 TEXT，存储整数字符串
- 应用层通过 `_cast_speaker()` 转 int，但 SQL 查询时仍为字符串比较
- 当前方案可工作，`_cast_speaker()` 保证了应用层一致性

### 10.2 DubManifest.speaker 类型

- `DubUtterance.speaker` 声明为 `str`
- `dub_manifest_from_utterances()` 中显式 `speaker=str(u.get("speaker", ""))` 转换
- 因为 DB 读出的 speaker 经 `_cast_speaker()` 已是 int，必须显式 str() 才能匹配 voice_assignment 的 str key

### 10.3 _probe_duration_ms 重复定义

- `translate.py`, `tts.py`, `mix.py` 各有一份相同的 `_probe_duration_ms()` 函数
- 应提取为公共 util

### 10.4 utterances.start_ms / end_ms 不存储

- `get_utterances()` 每次从 junction + cues 实时计算 start_ms/end_ms
- utterances 表无 start_ms/end_ms 列
- 如果查询频繁可考虑冗余存储，但当前性能可接受

### 10.5 RemoteStore 写入批量化

- translate phase 通过 RemoteStore 约 910 次 HTTP 调用（300 cues 的翻译场景）
- 可缓冲 `update_cue`/`update_utterance`，定期批量 flush 降到 ~10 次
- 当前单次 HTTP 开销（~50ms）相比 LLM 延迟（2-5s/次）可接受（~5%）

---

## 附录：JSON → DB 迁移记录

> 2026-03 从 JSON 文件驱动的 PhaseRunner 升级到 DB-First + Task 队列架构。

| 旧 (JSON) | 新 (DB 表) | 说明 |
|-----------|-----------|------|
| `dub.json` (segments) | `cues` 表 | 原子段，用户可编辑 |
| `dub.json` (segments merged) | `utterances` + `utterance_cues` | 分组壳 + TTS 缓存 |
| `roles.json` | `roles` 表 | 角色声线映射 |
| `manifest.json` (phases) | `tasks` 表 | 任务队列 |
| `manifest.json` (artifacts) | `artifacts` 表 | 文件注册表 |
| `names.json` / `slang.json` | `glossary` 表 | 术语表 |
| — | `events` 表 | 审计日志（新增） |

**移除的 Phase**：
| Phase | 原因 | 替代 |
|-------|------|------|
| reseg | 已移除 | v3.0 双源校准替代了单源 reseg |
| align | 职责拆分 | drift check → tts, SRT 生成 → burn |
