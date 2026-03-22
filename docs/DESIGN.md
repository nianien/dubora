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
| asr | 双源 ASR：Doubao VAD (word-level) + Gemini (分段/说话人/情绪) | Doubao ASR + Gemini |
| parse | Gemini 骨架 + Doubao 文本 → LLM 校准 → end_ms 延长 → DB cues | Gemini LLM |
| translate | 增量翻译 (utterance 级, per-cue 回填) | OpenAI / Gemini |
| tts | 语音合成 (增量, voice_hash 判脏) + drift_score 检查 | VolcEngine seed-tts-1.0 |
| mix | 混音 (adelay timeline placement) | FFmpeg |
| burn | 从 DB cues 生成 en.srt + 烧字幕到视频 | FFmpeg subtitles filter |

**数据流**：

```
extract → audio.wav, vocals.wav, accompaniment.wav (文件)
asr     → asr-doubao.json + asr-gemini.json (双源原始响应)
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
|   +-- asr-gemini.json                 #   Gemini ASR 结果
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
    speaker      TEXT NOT NULL DEFAULT '',        -- 实际存 roles.id 的整数字符串
    emotion      TEXT NOT NULL DEFAULT 'neutral',
    gender       TEXT,
    kind         TEXT NOT NULL DEFAULT 'speech',  -- 'speech' 或 'singing'
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

**字段约定：**
- `text`: 中文原文，来自 ASR，用户可编辑。
- `text_en`: 英文翻译，由 translate phase 回填到 cue 上。burn phase 直接读 cues.text_en 生成 en.srt。
- `speaker`: SQLite 列类型为 TEXT，但存储 roles.id 的整数字符串 (如 "10001")。应用层通过 `_cast_speaker()` 读取时转为 int。

**diff_and_save 机制：**
```
用户编辑 cue → diff_and_save() → 对比 _SOURCE_FIELDS (text/speaker/timing/emotion/kind)
  源字段变了 → 触发 calculate_utterances → 可能产生新 utterance (source_hash=NULL → 触发重翻)
  text_en 变了 → 更新 voice_hash → TTS 判脏
```

### 3.3 utterances 表 — 分组壳 + TTS 缓存

```sql
CREATE TABLE utterances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id      INTEGER NOT NULL REFERENCES episodes(id),
    text_cn         TEXT NOT NULL DEFAULT '',    -- 冗余缓存：合并自 sub-cues
    text_en         TEXT NOT NULL DEFAULT '',    -- 冗余缓存：合并自 sub-cues text_en
    speaker         TEXT NOT NULL DEFAULT '',    -- roles.id 整数字符串
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
- `source_hash`: 翻译判脏。SHA256(合并 sub-cue text)[:16]。translate phase 翻译成功后写入。
- `voice_hash`: TTS 判脏。SHA256(text_en|speaker|emotion)[:16]。TTS phase 合成成功后写入。
- `tts_policy`: JSON 字符串 `{"max_rate": 1.3, "allow_extend_ms": 500}`。由 translate phase 根据 utterance 间隙计算。
- `start_ms`, `end_ms`: **不存储在表中**，由 `get_utterances()` 从 junction 关联的 cues 实时计算。

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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id    INTEGER NOT NULL REFERENCES dramas(id),
    name        TEXT NOT NULL,         -- 角色名 (如 "平安")
    voice_type  TEXT NOT NULL DEFAULT '', -- 音色 ID (如 "zh_male_jieshuonansheng_mars_bigtts")
    role_type   TEXT NOT NULL DEFAULT 'extra', -- 'lead' / 'supporting' / 'extra' / 'narrator'
    UNIQUE(drama_id, name)
);
```

**speaker 用 role.id 的完整链路：**
```
ASR 输出 speaker="0","1" → parse phase: ensure_role(drama_id, "0") → role.id=10001
→ cue.speaker = "10001" (TEXT 列存整数字符串)
→ 前端读取: _cast_speaker() → speaker=10001 (int)
→ SpeakerBadge 显示: roles.find(r => r.id === cue.speaker)?.name
→ TTS: dub_manifest_from_utterances → DubUtterance.speaker=str(10001)="10001"
→ voice_assignment["speakers"]["10001"] = {voice_type: "..."}
→ volcengine.py: speakers.get(speaker) → 匹配
```

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
| **实现** | FFmpeg（提取）+ Demucs htdemucs v4（分离） |

Demucs 是 pipeline 中最慢的环节（2 分钟音频需 3-10 分钟 CPU），但显著提升 ASR 准确率和混音质量。

### 4.2 ASR（双源语音识别）

| | |
|---|---|
| **输入** | `extract.audio`（可配置为 `extract.vocals`） |
| **输出** | `asr.doubao` (Doubao VAD 原始响应), `asr.gemini` (Gemini ASR 结果) |
| **服务** | Doubao ASR (ByteDance) + Google Gemini |
| **预设** | Doubao: `asr_vad_spk`, Gemini: 配置项 `asr_gemini_model` |

**双源分工**：
- **Doubao VAD**: word 级时间戳 + 方言文本准确（作为文本 SSOT）
- **Gemini**: 分段骨架 + 说话人区分 + 情绪标注（文本会被 Doubao 替换）

**流程**：
1. 音频同时上传至 TOS + GCS（同一个 blob_key）
2. 并发调用 Doubao VAD + Gemini ASR（各自用 store 获取签名 URL）
3. 保存双源原始响应：`asr-doubao.json` + `asr-gemini.json`
4. ASR 热词从 DB glossary 表 (type='name') 自动加载

### 4.3 Parse（双源校准 → DB cues）

| | |
|---|---|
| **输入** | `asr.doubao` (Doubao VAD 原始响应), `asr.gemini` (Gemini ASR 结果) |
| **输出** | DB cues 表 (写入 cues), `asr-calibrated.json` (排查用), `asr-result.json` (最终 cue rows) |
| **核心逻辑** | Gemini 骨架 + Doubao word 级文本 → 时间窗口截取 → LLM 语义裁剪 → emotion 回填 → end_ms 延长 → 写 DB |

**流程**：
1. 读取双源结果：`asr-doubao.json` + `asr-gemini.json`
2. 时间窗口截取（`extract_vad_windows`）：按 Gemini 每条的 [start_ms-buffer, end_ms+buffer] 从 Doubao word 流截取局部 VAD 文本，生成 `{idx, gemini_text, vad_text}` 配对
3. LLM 校准（`llm_calibrate`）：LLM 在每条的 vad_text 范围内做语义裁剪替换（VAD 为准，Gemini 可能普通话意译）
4. 落盘 `asr-calibrated.json`（LLM 原始返回，排查用）
5. emotion 回填（`fill_null_emotions`）：从相邻同 speaker 段继承
6. end_ms 延长（`extend_end_ms`）：保证字幕最小显示时长
7. 清空旧 cues + utterances，全量写入新 cues

Parse 完成后进入 `source_review` 门控，等待人工在 IDE 中校准。

### 4.4 calculate_utterances — Greedy Merge

由 `store.calculate_utterances()` 执行（在 translate phase 和 diff_and_save 中调用）。

```
cues (按 start_ms 排序) → 贪心合并:
  同 speaker + 同 emotion + gap ≤ 500ms + 总时长 ≤ 10000ms → 合入同组
  否则 → 新组

每组算 cue_id 集合 (frozenset)，与 DB 现有 utterance 的 cue_id 集合对比:
  - 匹配 → 保留 (TTS 缓存复用)
  - 不匹配 → 新建 utterance (source_hash=NULL → 标记为脏 → 触发翻译)
  - 多余 → 删除
```

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
| **服务** | 火山引擎 TTS (VolcEngine seed-tts-1.0) |

**声线映射（DB roles 表）**：

```
roles 表: {id: 10001, name: "平安", voice_type: "en_male_hades_moon_bigtts", role_type: "lead"}
```

解析链路：
- `roles_map: {role_id → voice_type}`
- TTS 前检查所有 speaker(role_id) 是否在 roles 表中有 voice_type
- 未分配 → 返回错误，提示在 Voice Casting 中完成分配

**合成流程**：
1. 读所有 utterances，构建 full DubManifest
2. `get_dirty_utterances_for_tts()`: 找脏行 (voice_hash 不匹配)
3. 无脏行 → no-op
4. 并行逐句合成（默认 4 workers）
5. 静音裁剪 + 语速调整（超 budget 加速到 max_rate 1.3x）
6. 更新 DB: audio_path, tts_duration_ms, tts_rate, voice_hash
7. Drift score 检查: tts_duration_ms / physical_ms > 1.1 则警告

**voice_assignment 构建 (str-keyed)：**
```python
# roles_map: {10001: "zh_male_..."} (int → str)
str_roles_map = {str(k): v for k, v in roles_map.items()}
# processor 中: voice_assignment["speakers"]["10001"] = {"voice_type": "zh_male_..."}
# DubManifest 中: DubUtterance.speaker = str(u.get("speaker", "")) = "10001"
# volcengine.py: speakers.get(speaker) 用 speaker str key 查找，匹配
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
- Singing cues (kind='singing') 保留原始人声时间窗
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
    """子 cue 内容指纹 (text + timing + speaker + emotion) 的 SHA256[:16]。"""
    parts: list[str] = []
    for c in src_cues:
        parts.append(c.get("text", ""))
        parts.append(str(c.get("start_ms", 0)))
        parts.append(str(c.get("end_ms", 0)))
        parts.append(str(c.get("speaker", "")))
        parts.append(c.get("emotion", "neutral"))
    return sha256("|".join(parts).encode()).hexdigest()[:16]
```

**触发翻译的条件 (get_dirty_utterances_for_translate)：**
1. `source_hash IS NULL` → 从未翻译 (新 utterance)
2. `source_hash != _compute_source_hash(当前 sub-cues)` → 内容变了
3. 任何 sub-cue 的 `text_en` 为空 → 翻译不完整

**source_hash 写入时机：** translate phase 翻译成功后。不在 calculate_utterances 中更新，保证新 utterance 的 source_hash=NULL 自动标记为脏。

### 5.2 voice_hash — TTS 判脏

```python
def _compute_voice_hash(text_en: str, speaker: str = "", emotion: str = "") -> str:
    """text_en + speaker + emotion 的 SHA256[:16]。"""
    data = f"{text_en}|{speaker}|{emotion}"
    return sha256(data.encode()).hexdigest()[:16]
```

**触发 TTS 的条件 (get_dirty_utterances_for_tts)：**
- `voice_hash != _compute_voice_hash(当前 text_en, speaker, emotion)`
- 即 text_en、speaker(role_id)、emotion 任一变化 → 重新合成

**voice_hash 写入时机：** TTS phase 合成成功后。

### 5.3 diff_and_save — 前端编辑判脏

```python
_SOURCE_FIELDS = ("text", "speaker", "start_ms", "end_ms", "emotion", "kind")
# diff_and_save: 对比上述字段
# 源字段变化 → reset to source_review → calculate_utterances → 可能产生新 utterance
# text_en 变化 → reset to translation_review → sync_utterance_text_cache → voice_hash 更新
```

源字段变化 → `calculate_utterances()` 可能生成新 utterance → source_hash=NULL → 触发翻译。

### 5.4 sync_utterance_text_cache

用户在前端编辑 cue.text_en → `diff_and_save()` → `sync_utterance_text_cache()`:
- 重算 utterance 的 text_cn/text_en 缓存
- 如果 text_en 变了 → 更新 voice_hash → TTS 判脏

---

## 6. 前端显示规则

### 6.1 数据类型

```typescript
interface Cue {
  speaker: number      // roles.id FK (从 API 返回时已转 int)
  // ...
}

interface Role {
  id: number
  name: string
  voice_type: string
  role_type: string    // 'lead' | 'supporting' | 'extra' | 'narrator'
}
```

### 6.2 Speaker 显示

- **SpeakerBadge**: `roles.find(r => r.id === cue.speaker)?.name ?? String(cue.speaker)`
- **颜色**: `deriveSpeakers(cues)` 返回 `number[]` (去重, 保持出现顺序)，speaker 在数组中的 index 决定颜色
- **无效角色**: `roles.length > 0 && !roles.some(r => r.id === cue.speaker)` → 红色边框警告
- **切换角色**: 下拉列表展示 `role.name`，选中后 `updateCue(id, { speaker: role.id })`
- **新建角色**: 先 `saveRoles()` 获取真实 id，再 `updateCue(id, { speaker: newId })`

### 6.3 Roles API

```
GET  /episodes/{drama}/roles  → {"roles": [{id, name, voice_type, role_type}, ...]}
PUT  /episodes/{drama}/roles  → body: {"roles": [{id?, name, voice_type, role_type?}, ...]}
     有 id → 更新, 无 id → 新建, 缺失 → 删除
```

### 6.4 快捷键 (useKeyboard)

- 1-9: 快速切换 speaker (`speakerList[idx]`，已是 number)
- Ctrl+B: 分割 cue
- Ctrl+M: 合并 cue
- Ctrl+I: 插入空 cue (默认 speaker: `refCue?.speaker ?? 0`)
- Delete/Backspace: 删除 cue
- Alt+Arrow: 微调 cue 边界 ±50ms
- Shift+Alt+Arrow: 微调 cue 边界 ±200ms

### 6.5 Undo/Redo

- Command 模式: `{apply, inverse, description}`
- 所有 cue 操作通过 `useUndoableOps()` hook
- `changeSpeaker(id, oldSpeaker: number, newSpeaker: number)`

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
