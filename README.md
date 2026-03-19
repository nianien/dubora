# Dubora

中文短剧自动配音流水线。输入中文视频（无需剧本），输出英文配音版本，包含多角色语音合成、硬烧英文字幕、保留背景音乐。

## 流水线

```
阶段:  提取      识别              翻译              配音        合成
Phase: extract → asr → parse  →  translate  →  tts → mix  →  burn
Gate:                        ↑              ↑
                      source_review   translation_review
```

7 个 phase，2 个人工审阅 gate，基于 SHA256 指纹的增量执行。

## 快速开始

```bash
# 安装（按角色选择）
make install-all        # 全部依赖（本地开发推荐）
make install-pipeline   # core + pipeline
make install-web        # core + web

# 提交流水线任务
vsd-pipeline run 家里家外 5 --to burn

# 启动 worker 执行任务
vsd-pipeline worker

# 启动 Web 服务器
vsd-web serve --port 8765
```

## 命令行

```bash
vsd-pipeline run 家里家外 5 --to burn       # 提交流水线任务
vsd-pipeline run 家里家外 5 --from tts      # 从指定阶段重跑
vsd-pipeline worker                         # 启动任务执行器
vsd-pipeline phases                         # 列出所有 phase
vsd-web serve --port 8765                   # Web 服务器
```

## 校准 IDE

基于 Web 的 ASR 校准工具，用于翻译和配音前的字幕校准。

- 可视化段落编辑：文本（中英文）、说话人、情绪、时间轴
- 段落拆分 / 合并 / 插入 / 删除，支持撤销重做
- 视频同步播放与字幕叠加
- 流水线控制面板：运行、取消、从任意阶段重跑
- 配音视频播放，支持 A/B 对比
- 角色选角：为角色分配 TTS 音色，内联试听

详见 [docs/IDE-GUIDE.md](docs/IDE-GUIDE.md) 操作手册。

## 架构

```
Monorepo:
  packages/core/       → dubora-core     (数据层: config, DbStore, utils, events)
  packages/pipeline/   → dubora-pipeline (执行层: phases, processors, models)
  packages/web/        → dubora-web      (API 层: FastAPI REST + Worker API)
```

- **Phase**：编排层（DB I/O、manifest 更新、错误处理）
- **Processor**：无状态业务逻辑（纯计算，可独立测试）
- **DbStore**：SQLite DB 是所有元数据的 SSOT

详见 [docs/DESIGN.md](docs/DESIGN.md) 技术设计。

## 数据布局

所有数据由 `DATA_DIR`（默认 `data/`）统一管理：

```
data/
├── db/dubora.db                          # SQLite DB (env: DB_DIR)
├── pipeline/{剧名}/{集号}/               # 集级工作区
│   ├── {集号}.wav, *-vocals.wav, ...     #   音频文件（workdir 根目录）
│   ├── asr-result.json                   #   ASR 结果
│   ├── tts/segments/                     #   TTS 逐句音频
│   └── output/                           #   最终交付物（配音视频、SRT 字幕）
├── gcs/                                  # GCS 缓存
├── tos/                                  # TOS 缓存
└── .cache/                               # faststart、voice-preview 缓存
```

## 外部服务

| 服务 | 用途 | 环境变量 |
|------|------|----------|
| 豆包 ASR（字节跳动） | 语音识别 + 说话人分离 | `DOUBAO_APPID`, `DOUBAO_ACCESS_TOKEN` |
| 火山引擎 TOS | ASR 音频上传存储 | `TOS_ACCESS_KEY_ID`, `TOS_SECRET_ACCESS_KEY` |
| 火山引擎 TTS | 英文语音合成 | 同豆包 |
| Google Gemini | 翻译（默认引擎） | `GEMINI_API_KEY` |
| OpenAI | 翻译（备选）+ 断句优化 | `OPENAI_API_KEY` |
| Demucs | 人声分离 | 本地 |
| FFmpeg | 音视频处理 | 本地 |

## 开发

```bash
make install-dev     # 安装开发依赖
make test            # 运行测试
make lint            # Ruff 检查
make clean           # 清理缓存
```

## 许可证

私有 / 保留所有权利。
