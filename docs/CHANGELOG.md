# Changelog

## 2026-06-03

### ASR 方案统一：豆包 Seed-ASR 2.0 单源 + Gemini scene context

**核心理念**：移除原 doubao + tencent + fish + gemini 多源融合方案，统一为单源豆包，配 Gemini 视频场景分析做上下文辅助。

**为什么**：实测发现豆包 Seed-ASR 2.0 + Gemini 自动生成的业务场景上下文（注入 `corpus.context` dialog_ctx）能将短剧/广告/ASMR 等领域音频的识别准确率从 ~50% 拉到 ~92%。多源融合（doubao+tencent+fish+LLM对齐）的复杂度不再必要。

**ASR phase（v5.0.0）**：

- 删除 `_check_gaps_and_get_extras`、`extra_models` 并发调用、`_make_task` 多模型分支
- 单源豆包，配 `_ensure_scene_description` 调 Gemini 听音频生成 200 字业务场景描述
- 上下文缓存到 `workspace/asr-context.json`，幂等复用

**parse phase（v5.0.0）**：

- 删除 `_run_doubao_fusion`（含 LLM diff/align）、`_run_direct` 双分支
- 直接读 `asr-doubao.json` → `get_doubao_utterances()` → emotion 回填 + end_ms 延长 → cues

**关键修复**：

- `doubao/presets.py` 删 `ssd_version="200"` —— 这是触发豆包服务端"对话结束检测" bug 的元凶，对快语速念清单类音频会在 ~32s 处提前终止
- `CorpusConfig.from_scene()` —— 把业务场景作为 dialog_ctx 注入

**删除的文件**：

- `processors/asr/fusion.py`（三源融合 + LLM 对齐）
- `processors/asr/tencent.py`、`processors/asr/openai.py`
- `models/gemini/asr_client.py`（Gemini 不再做 ASR，只做 scene context）

**新增的文件**：

- `models/gemini/scene_context_client.py`：Gemini 听音频 → 业务场景描述
- `processors/asr/postprocess.py`：从 fusion.py 抽离的 emotion 回填 + end_ms 延长 helper

**配置清理**：

- `settings.py` 删 `asr_primary` / `asr_models` / `asr_gemini_model`
- `.env` 删 `ASR_PRIMARY` / `ASR_MODE` / `TENCENT_SECRET_*`
- `manifest.py` artifact 只剩 `asr.doubao`

**Doubao 模型层**：

- `CorpusConfig.from_scene()` 业务场景上下文，dialog_ctx 格式
- `get_preset(scene_description=...)` 透传支持

## 2026-06-01

### Speaker / Role 分离 + Fish 自动克隆

**核心理念**：依据第一性原理重新划分"声源"和"语义角色"。决定音色的是 speaker（物理声源），role 是 speaker 的可选语义分组。两者关系不强制 1:1。

**Schema 变更**：

- `cues` 表新增 `role_id INTEGER REFERENCES roles(id)`，可空
- `utterances` 表新增 `role_id INTEGER REFERENCES roles(id)`，可空（冗余缓存，从 group[0] 派生）
- `cues.speaker` / `utterances.speaker` 字段语义恢复：始终是 ASR 原始 label（"0", "1"...），永不被覆写
- `roles` 表无 schema 变更，但增加约定：每个 drama 自动维护 `name="默认"` 的 role 作为 TTS fallback

**音色决策**：

```
effective_role_id = cue.role_id or default_role.id   # name='默认' 约定
→ roles[effective_role_id].voice_type / sample_audio
```

**Utterance 合并规则**（`store.calculate_utterances`）：

```
都有 role_id → 看 role_id 一致性（speaker 可不同，支持修正 ASR 错分）
都没 role_id → 看 speaker 一致性
一有一无 → 不合并
```

**Parse phase 改动**：

- 写 cue 时直接存 ASR speaker label，不映射到 role
- 完成后 `ensure_role(drama_id, "默认")` 幂等创建 fallback role

**TTS phase 改动**：

- `_check_voice_assignment` 改为按 role_id 检查；fallback role 存在时不再 fail
- Fish 模式 `_ensure_role_samples` 自动截取规则：普通 role 按 `cue.role_id == role.id` 找；默认 role 不限 role_id，从全集 vocals 选最优 cue
- 引入 `TTS_ENGINE` 环境变量切换引擎（`volcengine` / `fish`），不再需要改源码

**Dirty 判脏**：

- `_compute_source_hash` 用 `role_id` 取代 `speaker`
- `_compute_voice_hash` 用 `role_id` 取代 `speaker`
- `_SOURCE_FIELDS` 用 `role_id` 取代 `speaker`

**前端**：

- `Cue.speaker: number → string`（恢复 ASR label 语义），新增 `Cue.role_id: number | null`
- `SpeakerBadge` 显示格式：`{role_name or "未分配"}({speaker_label})`
- 点 badge 改 `cue.role_id`（不再改 `cue.speaker`）；speaker label 只读
- 着色按 role_id 分组（未分配统一灰色）
- `useUndoableOps.changeSpeaker → changeRole`
- 快捷键 1-9 选 role（按 roles 列表 index）

**迁移**：

- Schema：`ALTER TABLE` 加 `role_id` 列（默认 NULL）
- 数据：历史上 `cue.speaker` 被前端改成 role.id 的剧集需要反向修复（`cue.role_id ← cue.speaker`，`cue.speaker` 设为 ""）；纯 ASR label 状态的剧集无需迁移数据

**已知限制**：

- 一个 role 关联多个 speaker 时，Fish 克隆只能用单一 sample，音色被统一（实用妥协）
- 跨集 speaker 一致性未引入（per-episode label，将来按需聚合）

## 2026-03-13

### 用户系统 + 多账户隔离

- 新增 `users` + `user_auths` 表，支持 Google OAuth 登录 + dev 模式
- `dramas` 表添加 `user_id NOT NULL` 列，UNIQUE 约束改为 `UNIQUE(user_id, name)`
- 登录时自动创建/更新用户记录，cookie 存 `user_id`
- `AuthMiddleware` 认证通过后注入 `request.state.user_id`
- 新增权限工具函数：`get_user_id()`, `require_drama_owner()`, `require_episode_owner()`
- 所有 API 过滤当前用户数据 + 写操作校验所有权（403）
- Worker API (`/api/worker/*`) 不加权限校验
- 鉴权未启用时 user_id=None，所有隔离/校验逻辑跳过

### 修复

- cues API 的 GET 端点不再自动创建 drama/episode（改为 lookup + 404）
- roles PUT 端点不再自动创建 drama（改为 lookup + 404）
- `list_episodes` 批量 cue/artifact 查询改为 JOIN 过滤，不再全表扫描
- `update_cue` / `update_utterance` 添加字段白名单，防止 SQL 注入
- `voice_hash` 计算中 emotion 默认值统一为 `"neutral"`
- `update_drama` 更新时写入 `updated_at`
- media API faststart 缓存 key 使用完整路径避免碰撞，temp 文件放在 cache 目录避免跨设备 rename 失败
- `_derive_stages` 处理空 phases 返回 `"pending"` 而非 `"succeeded"`
- cues 表移除 `cv` 列，`diff_and_save` 改为直接对比 `_SOURCE_FIELDS`
- Schema v6: `dictionary` 重命名为 `glossary`，`artifacts` 字段改为 `kind/gcs_path/checksum`

## 2026-03-12

### Monorepo 拆包

- 项目从单包重构为 3 包 monorepo：`dubora-core`（数据访问层）、`dubora-pipeline`（执行层）、`dubora-web`（API 层）
- CLI 拆分为 `vsd-pipeline`（pipeline 命令）和 `vsd-web`（Web 服务器）
- Schema、Types、Phase 基类从 core 移到 pipeline，core 瘦身为纯数据访问层
- `PipelineStore` 重命名为 `DbStore`

### Worker API + 远程模式

- 新增 Worker API（25 个端点），支持 task worker 通过 HTTP 访问数据库
- 新增 `RemoteStore`，实现与 `DbStore` 相同接口的 HTTP 代理
- `vsd-pipeline worker --api-url` 支持远程模式，worker 和 web 可部署在不同机器
- Reactor 调度逻辑集中在 web 侧（`/complete` 和 `/fail` 端点内部运行）

### GCP 部署

- 新增 `deploy/deploy-web.sh` 和 `deploy/deploy-task.sh` 部署脚本
- 新增 `deploy/docker-compose.yml`，web + worker 双容器编排
- Dockerfile.web：轻量 Python 镜像（无 PyTorch），含前端构建
- Dockerfile.task：完整 pipeline 镜像（PyTorch + FFmpeg），默认远程模式
- 新增 Cloud Build 配置（`cloudbuild-web.yaml`、`cloudbuild-task.yaml`）

### 修复

- Media API 添加 GCS 下载 fallback，容器内可访问 GCS 视频文件
- Dubbed 视频播放改用 `/api/export/{episodeId}/dubbed.mp4`，支持 GCS signed URL fallback
- `pyproject.toml` 添加 `package-data` 声明，确保 JSON/YAML 资源文件打包
- Pipeline 状态推导兼容无 task 记录的已完成 episode（legacy 数据）
- Deploy 脚本添加 GCS 凭证路径映射

## 2026-03-02

### Pipeline

- 合并 parse + reseg 为单个 phase，减少 dub.json 多阶段写入冲突
- should_run 改为逐 artifact 一致性校验，替代 composite inputs_fingerprint
- 删除 reseg phase（processor 层保留），清理 compute_inputs_fingerprint
- parse 阶段增加 LLM emotion 修正，reseg 断句后根据台词语义修正情绪标注
  - 新增 `emotion_correct` processor 和 prompt 模板
  - 提取 `_create_llm_fn()` 供 reseg 和 emotion_correct 共用
  - 支持 `phases.parse.emotion_correct_enabled` 开关（默认开启）
- ASR 热词从 `names.json` 自动加载，移除 `PipelineConfig.doubao_hotwords` 硬编码

### 翻译 (MT)

- 翻译 prompt 支持故事背景注入，自动读取 `{drama_dir}/story_background.txt`
- 移除硬编码的 plot_overview 默认值，清理整条链路的 plot_overview 参数
- system prompt 从 "crime drama" 改为 "Chinese TV drama"（通用化）

### 工程

- `emotions.json` 从 `src/dubora/config/` 迁移到项目根 `resources/`
- 新增 `PROJECT_ROOT` 常量（通过查找 `pyproject.toml` 定位），替代脆弱的 `parents[N]` 路径

### Web IDE

- 段落面板时间显示：左侧改为开始时间 + 结束时间，右侧保留时长，修复重复显示问题

#### 快捷键优化

- 方向键操作时自动暂停播放，便于精确对齐
- 新增 Shift+Alt+方向键：segment 边界直接对齐到光标位置
- Alt+方向键调整精度从 100ms 改为 50ms
- 播放 seek 精度从 100ms 改为 50ms

#### 段落操作优化

- 拆分段落时按标点符号切分，移除分割点的标点
- 生成 dub.json 时自动去除每句末尾的逗号和句号

#### 时间轴交互优化

- 点击 segment 只选中，不移动播放光标
- 播放光标靠近窗口左右 10% 区域时自动滚动居中
- segment 标签不再显示说话人前缀，节省空间
- 选中 segment 的活跃边界手柄高亮为黄色

#### 段落列表优化

- 显示起始时间 + 时长（双行显示）
- 角色不在 roles 中时显示灰色标记

## 2026-03-01

### Web IDE

#### 角色管理

- 角色下拉列表按拼音排序
- 角色/音色映射页面支持内联添加和删除角色

#### 数据保存

- 新增自动保存：修改后 2 秒自动保存
- Ctrl+S 手动保存

#### 基础快捷键

- Space 播放/暂停
- Enter 跳转下一段并播放
- 方向键上下切换段落
- 方向键左右微调播放进度
- Alt+方向键微调 segment 起止时间（光标位置决定调 start 还是 end）
- Ctrl+B 拆分段落，Ctrl+M 合并段落
- Ctrl+I 插入空段落，Delete 删除段落
- 数字键 1-9 快速切换说话人
- N/A/S/E/I/F 快速切换情绪

#### 撤销/重做

- Ctrl+Z 撤销，Ctrl+Shift+Z 重做
- 所有段落操作（编辑、拆分、合并、插入、删除）均支持撤销
