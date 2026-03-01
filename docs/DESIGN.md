# Dubora - 国产短剧本地化方案

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
- 可重跑：每步产物落盘，支持局部重跑和人工干预

---

## 2. 系统架构

### 2.1 Pipeline 总览

```
demux → sep → asr → sub → [人工校验] → mt → align → tts → mix → burn
  |       |      |      |                  |      |       |      |      |
  |       |      |      |                  |      |       |      |      +-- burn.video (成片)
  |       |      |      |                  |      |       |      +-- mix.audio (混音)
  |       |      |      |                  |      |       +-- tts/segments/ (逐句音频)
  |       |      |      |                  |      +-- dub.model.json (SSOT)
  |       |      |      |                  +-- mt_output.jsonl
  |       |      |      +-- subtitle.model.json (SSOT)
  |       |      +-- asr-result.json
  |       +-- vocals.wav / accompaniment.wav
  +-- audio.wav
```

9 个阶段严格线性执行，通过 `manifest.json` 记录状态和指纹，支持增量跑。

### 2.2 整体分层

```
+-------------------------------------------------------------+
|  CLI (cli.py)                                               |
|  run / bless / fix / phases ; config_to_dict -> RunContext  |
+-----------------------------+-------------------------------+
                              |
+-----------------------------v-------------------------------+
|  Pipeline Framework (pipeline/core/)                        |
|  PhaseRunner, Manifest, Fingerprints, Atomic                |
+-----------------------------+-------------------------------+
                              |
+-----------------------------v-------------------------------+
|  Phases (pipeline/phases/)       | Processors (stateless)   |
|  demux->sep->asr->sub->mt->     | 纯计算、可单测            |
|  align->tts->mix->burn          |                           |
+-----------------------------+-------------------------------+
                              |
+-----------------------------v-------------------------------+
|  Schema (schema/)  | Config (config/)  | Models / Infra    |
|  SSOT 数据模型      | PipelineConfig    | Doubao, OpenAI    |
+-------------------------------------------------------------+
```

- **Phase**：编排层，实现 `Phase` 抽象（`requires` / `provides` / `run`），负责文件路径解析、读入上游产物、调用 Processor、写回产物并返回 `PhaseResult`。
- **Processor**：无状态业务逻辑，只做计算，不直接依赖 manifest 或 workspace 路径约定，便于单测与替换。
- **Runner**：根据 manifest 与 fingerprint 决定是否执行、解析 inputs、分配 outputs、执行 phase、登记 artifact 与状态。

### 2.3 CLI 用法

```bash
vsd run video.mp4 --to burn                    # 全流程
vsd run video.mp4 --from mt --to tts           # 从 mt 强制重跑到 tts
vsd run video.mp4 --to burn                    # 增量跑（已完成的阶段自动跳过）
vsd bless video.mp4 sub                        # 手动编辑产物后刷新指纹
vsd fix video.mp4 asr                          # 从 asr-result.json 重新生成 asr.fix.json
vsd run videos/drama/4-70.mp4 --to burn        # 批量模式（4-70 集）
```

### 2.4 核心数据流（三个 SSOT）

| SSOT | 产出阶段 | 消费阶段 | 说明 |
|------|---------|---------|------|
| `asr-result.json` | asr | sub | ASR 原始响应，包含 word 级时间戳、speaker、emotion |
| `subtitle.model.json` | sub | mt, align | 字幕数据源，utterance + cue 结构，支持人工校验 |
| `dub.model.json` | align | tts, mix | 配音时间轴，包含翻译文本、时长预算、voice 映射 |

### 2.5 文件布局

```
videos/{剧名}/                  # 剧级目录
+-- 1.mp4                      # 原视频
+-- dub/
    +-- dict/                           # 剧级词典 + 声线配置
    |   +-- roles.json                  #   统一声线映射（roles + default_roles）
    |   +-- slang.json                  #   行话词典
    |   +-- names.json                  #   人名词典（中文 -> 英文）
    +-- 1/                              # 集级 workspace
        +-- manifest.json               # Pipeline 状态机
        +-- source/                     # SSOT（人工可编辑）
        |   +-- asr-result.json         #   ASR 原始输出
        |   +-- asr.fix.json            #   人工校准层（speaker/text/时间轴修正）
        |   +-- subtitle.model.json     #   字幕 SSOT（bless 后可手改）
        |   +-- dub.model.json          #   配音 SSOT（align 生成）
        +-- derive/                     # 确定性派生（可重算）
        |   +-- subtitle.align.json     #   时间对齐结果
        |   +-- voice-assignment.json   #   声线分配快照
        +-- mt/                         # 翻译产物（LLM 输出）
        |   +-- mt_input.jsonl
        |   +-- mt_output.jsonl
        +-- tts/                        # 合成产物
        |   +-- segments/               #   逐句 TTS 音频
        |   +-- segments.json           #   段索引
        |   +-- tts_report.json
        +-- audio/                      # 声学工程
        |   +-- 1.wav                   #   原始音频
        |   +-- 1-vocals.wav            #   人声
        |   +-- 1-accompaniment.wav     #   伴奏
        |   +-- 1-mix.wav               #   最终混音
        +-- render/                     # 最终交付物
            +-- en.srt                  #   英文字幕
            +-- zh.srt                  #   中文字幕
            +-- 1-dubbed.mp4            #   成片
```

目录按语义角色分层：`source/` 是人工可编辑的事实，`derive/` 是可重算的派生，`mt/`/`tts/` 是模型产物，`audio/` 是声学工程，`render/` 是最终交付。

---

## 3. Pipeline Framework

### 3.1 Phase 接口

每个 Phase 实现三个方法：

```python
class Phase(ABC):
    name: str
    version: str  # 逻辑变更时递增，触发重跑

    def requires(self) -> List[str]:   # 输入 artifact keys
    def provides(self) -> List[str]:   # 输出 artifact keys
    def run(ctx, inputs, outputs) -> PhaseResult
```

Phase 只声明输出，Runner 负责路径分配、指纹计算和 manifest 注册。Phases 通过 `_LazyPhase` 延迟加载，避免在不需要的阶段导入重型依赖（如 torchaudio）。

### 3.2 增量执行（should_run 决策）

Runner 的 7 级检查决定是否跳过：

1. `force` 标记（`--from` 指定的阶段及之后）
2. manifest 中无记录 -> 跑
3. `phase.version` 变化 -> 跑
4. 输入 artifact 指纹变化（上游产物内容变了） -> 跑
5. config 指纹变化 -> 跑
6. **输出文件指纹不匹配** -> 跑（人工编辑会触发）
7. status != succeeded -> 跑

**`vsd bless` 命令**：人工编辑 subtitle.model.json 后，运行 `vsd bless video.mp4 sub` 刷新 manifest 中的输出指纹，避免 sub 阶段被重跑。

### 3.3 Processor / Phase 分离

- **Processor**（`pipeline/processors/`）：无状态纯业务逻辑，不做文件 I/O
- **Phase**（`pipeline/phases/`）：编排层，负责读输入、调 processor、写输出、更新 manifest

### 3.4 消除缓存幽灵的三条规则

| 规则 | 约定 |
|------|------|
| **Rule A** | 任何**影响输出的逻辑变更**，必须 bump 对应 `phase.version` |
| **Rule B** | 任何**影响输出的配置变更**，必须进入 config fingerprint |
| **Rule C** | 任何**人工修改 SSOT**，必须对对应 phase 执行 `vsd bless` |

---

## 4. 各阶段实现

### 4.1 Demux（音频提取）

| | |
|---|---|
| **输入** | 原视频 mp4 |
| **输出** | `demux.audio` -> WAV (16k, mono, PCM s16le) |
| **实现** | FFmpeg |

### 4.2 Sep（人声分离）

| | |
|---|---|
| **输入** | `demux.audio` |
| **输出** | `sep.vocals` (人声), `sep.accompaniment` (伴奏) |
| **实现** | Demucs htdemucs v4（本地 GPU/CPU） |

Demucs 是 pipeline 中最慢的环节（2 分钟音频需 3-10 分钟 CPU），但显著提升 ASR 准确率和混音质量。

### 4.3 ASR（语音识别 + 说话人分离）

| | |
|---|---|
| **输入** | `demux.audio`（可配置为 `sep.vocals`） |
| **输出** | `asr.asr_result` -> JSON (原始 ASR 响应) |
| **服务** | 豆包大模型 ASR (ByteDance) |
| **预设** | `asr_spk_semantic`（语义分句 + Speaker Diarization） |

**流程**：
1. 音频上传至 TOS（火山引擎对象存储），基于内容哈希去重
2. 调用豆包 ASR API（submit -> poll query）
3. 返回 word 级时间戳 + speaker 标签 + emotion/gender

### 4.4 Sub（字幕模型生成）

| | |
|---|---|
| **输入** | `asr.asr_result` |
| **输出** | `subs.subtitle_model` (SSOT v1.3), `subs.zh_srt` |
| **核心逻辑** | Utterance Normalization -> Subtitle Model Build -> SRT Render |

**双数据源模式**：当 `asr.fix.json` 存在时：
- Word 级时间轴来自 `asr-result.json`（时间骨架）
- Speaker/text 来自 `asr.fix.json`（人工校准层）
- 归一化用校准后的 speaker 做切分边界

**asr.fix.json 操作类型**：

| 操作 | 说明 | 示例 |
|------|------|------|
| 编辑 | 指定 idx，修改 text/speaker | `{"idx": 3, "speaker": "pa", "text": "..."}` |
| 拆分 | 同一 idx 多条 | `{"idx": 5, "text": "前半"}, {"idx": 5, "text": "后半"}` |
| 删除 | 原始 utterance 的 idx 不出现在 fix 中 | 跳过 idx=6 |
| 插入 | 指定 start/end 时间（支持 MM:SS 格式） | `{"speaker": "by", "text": "...", "start": "01:15", "end": "01:20"}` |

**Subtitle Model v1.3 结构**：

```json
{
  "schema": {"name": "subtitle.model", "version": "1.3"},
  "audio": {"duration_ms": 95480},
  "utterances": [
    {
      "utt_id": "utt_0001",
      "speaker": {
        "id": "pa",
        "gender": "male",
        "speech_rate": {"zh_tps": 4.2},
        "emotion": {"label": "sad", "confidence": 0.85, "intensity": "moderate"}
      },
      "start_ms": 5280,
      "end_ms": 6520,
      "text": "坐牢十年，",
      "cues": [
        {"start_ms": 5280, "end_ms": 6520, "source": {"lang": "zh", "text": "坐牢十年，"}}
      ]
    }
  ]
}
```

**Utterance Normalization**：ASR 的 utterance 边界不稳定，从 word 级时间戳重建边界：
- 基于静音间隔（>=450ms，可配置）拆分
- Speaker 变化硬边界：不同 speaker 的 word 永远不合并到同一 utterance
- 最大时长约束（默认 8000ms）
- 附加标点：从 utterance 文本反推附加到 word

**注意**：Sub 阶段不再自动注册 speaker。角色由用户在 IDE 中编辑 `dub.json` 的 speaker 字段后，手动在 `roles.json` 中配置声线。

### 4.5 MT（机器翻译）

| | |
|---|---|
| **输入** | `subs.subtitle_model`, `asr.asr_result` |
| **输出** | `mt.mt_input` (JSONL), `mt.mt_output` (JSONL) |
| **服务** | Google Gemini 2.0 Flash / OpenAI GPT-4o-mini |

**翻译策略**：
- 按 utterance 粒度逐句翻译
- 整集上下文从 `asr-result.json` 的 `result.text` 获取
- Per-utterance 词典匹配：只在当前句命中时才注入 glossary

**人名处理（NameGuard + DictLoader）**：
- **NameGuard**：从中文文本中提取人名，替换为占位符 `<<NAME_0:平安>>` 后发给 LLM
- **DictLoader**：管理 `dub/dict/` 下的词典文件
  - `names.json`：人名映射（`{"平安": "Ping An"}`，简单 key-value 格式）
  - `slang.json`：行话/术语词典
- names.json 的 key 自动同步到 NameGuard 白名单，确保人工添加的人名一定被识别
- LLM 自动补全未知人名（first-write-wins 策略，写入 names.json）

**词典系统** (`dub/dict/slang.json`)：
```json
{
  "三条": "three of a kind",
  "胡了": "I've won!",
  "给钱给钱": "Pay up!"
}
```

### 4.6 Align（时间轴对齐 + 重断句）

| | |
|---|---|
| **输入** | `subs.subtitle_model`, `mt.mt_output`, `demux.audio` |
| **输出** | `subs.subtitle_align`, `subs.en_srt`, `dub.dub_manifest` |

**核心职责**：
1. 将英文翻译映射回原始中文时间轴（不修改时间边界）
2. 计算 TTS 时长预算（`budget_ms = end_ms - start_ms`）
3. 动态 `allow_extend_ms`（不与下一句重叠）
4. 在 utterance 内重断句生成 en.srt
5. 生成 `dub.model.json`（TTS 和 Mix 的输入合约）
6. `audio_duration_ms` 通过 ffprobe 从实际音频获取（非推断）

**DubManifest 结构**（`source/dub.model.json`）：

```json
{
  "audio_duration_ms": 95480,
  "utterances": [
    {
      "utt_id": "utt_0001",
      "start_ms": 5280, "end_ms": 6520,
      "budget_ms": 1240,
      "text_zh": "坐牢十年，",
      "text_en": "Ten years in prison...",
      "speaker": "pa",
      "gender": "male",
      "emotion": {"label": "sad", "confidence": 0.85, "intensity": "moderate"},
      "tts_policy": {"max_rate": 1.3, "allow_extend_ms": 500}
    }
  ]
}
```

### 4.7 TTS（语音合成）

| | |
|---|---|
| **输入** | `dub.dub_manifest`, `roles.json` |
| **输出** | `tts.segments_dir`, `tts.segments_index`, `tts.report`, `tts.voice_assignment` |
| **服务** | 火山引擎 TTS (VolcEngine seed-tts-1.0) |
| **API 文档** | https://www.volcengine.com/docs/6561/1257544?lang=zh |
| **音色试听** | https://console.volcengine.com/speech/new/voices?projectName=default |

**声线映射（单文件 roles.json）**：

```json
{
  "roles":         { "PingAn": "en_male_hades_moon_bigtts", ... },
  "default_roles": { "male": "LrNan1", "female": "LrNv1", "unknown": "LrNan1" }
}
```

解析链路：
- 已标注：`role_id -> voice_type`（如 `PingAn -> en_male_hades_moon_bigtts`）
- 未标注：`default_roles[gender] -> voice_type`

**Voice Casting UI**（Web）：

IDE 内置 Voice Casting 页面，用于可视化管理 `roles.json`。入口在主界面右上角 "Voice Casting" 按钮，进入后主界面完全切换（独立 header，不显示剧集选择）。

- **左栏**：角色列表（来自 `roles.json` 的 `roles` + `default_roles`），选中角色后右栏高亮已分配音色
- **右栏**：音色目录（来自 `resources/voices.json`），支持按 Category / Gender 筛选
- **分配**：选中左栏角色 → 点击右栏音色卡片即完成分配 → Save 写回 `roles.json`
- **试听**：每个音色卡片有 ▶ 按钮播放官方 trial 音频
- **自定义合成**：每个音色卡片有 "Try" 按钮，展开内联面板（Emotion 下拉 + 文本输入 + Synthesize），调用 TTS API 即时合成试听，合成历史按音色分组显示
- **自动滚动**：选中已绑定音色的角色时，右栏自动滚动到对应音色卡片居中

注意：`roles.json` 是**剧级别**配置，Voice Casting 页面只需选择剧名，不涉及剧集。

**Voices API**（`src/dubora/web/api/voices.py`）：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/voices` | GET | 返回音色目录（解析 `resources/voices.json`） |
| `/api/voices/synthesize` | POST | 调用 VolcEngine TTS 合成，结果缓存在 `.cache/voice-preview/` |
| `/api/voices/history` | GET | 返回合成历史（来自缓存 manifest） |
| `/api/voices/audio/{key}` | GET | 提供缓存的 WAV 文件 |

合成结果基于 `SHA256(voice_id|text|emotion)` 去重缓存，避免重复调用 TTS API。

**合成流程**：
- 并行逐句合成（默认 4 workers）
- 静音裁剪（trim silence）
- 语速调整：若 TTS 时长超过 budget，加速到 max_rate（1.3x）
- Episode 级缓存：相同 text + voice 的 TTS 结果复用

**产物**：

| 产物 | 路径 | 说明 |
|------|------|------|
| `tts.segments_dir` | `tts/segments/` | 逐句 WAV 文件 |
| `tts.segments_index` | `tts/segments.json` | 段索引：utt_id -> wav/voice/duration/hash |
| `tts.voice_assignment` | `derive/voice-assignment.json` | 声线分配快照 |
| `tts.report` | `tts/tts_report.json` | 诊断报告 |

### 4.8 Mix（混音）

| | |
|---|---|
| **输入** | `dub.dub_manifest`, `tts.segments_dir`, `tts.report`, `sep.accompaniment` |
| **输出** | `mix.audio` |
| **实现** | FFmpeg adelay + amix |

**Timeline-First 架构**：
- 用 FFmpeg `adelay` 滤镜将每段 TTS 精确放置到时间轴位置
- 伴奏轨 + TTS 轨混合，TTS 播放时伴奏自动压低（ducking，10:1 压缩比）
- `apad + atrim` 强制输出与原音频等长
- 混音目标：-16 LUFS（EBU R128），True Peak -1.5 dB

### 4.9 Burn（字幕烧录）

| | |
|---|---|
| **输入** | `mix.audio`, `subs.en_srt` |
| **输出** | `burn.video` -> 最终成片 mp4 |
| **实现** | FFmpeg subtitles 滤镜硬烧 |

---

## 5. 人工校准层（asr.fix.json）

`source/asr.fix.json` 是人工对 ASR 结果的校准文件，**不在 manifest 中**（从磁盘直接读取，修改不会触发 ASR 重跑）。

**支持的时间格式**：
- 整数毫秒：`40920`
- MM:SS：`"00:40"`
- MM:SS.frac：`"00:40.9"`
- H:MM:SS：`"1:01:23"`

**示例**：
```json
{
  "schema": {"name": "asr.fix", "version": "1.0"},
  "utterances": [
    {"idx": 0, "speaker": "pa", "text": "我弃牌。"},
    {"idx": 1, "speaker": "wmz", "text": "白爷，他上了。"},
    {"speaker": "by", "text": "内心独白", "start": "01:15", "end": "01:20"}
  ]
}
```

- 有 `start`/`end` 的条目自动视为**插入**（忽略 idx 值）
- 同时支持 `start`/`end` 和 `start_ms`/`end_ms` 两种 key

---

## 6. 外部服务依赖

| 服务 | 用途 | 环境变量 |
|------|------|---------|
| **豆包 ASR** | 中文语音识别 + 说话人分离 | `DOUBAO_APPID`, `DOUBAO_ACCESS_TOKEN` |
| **火山引擎 TOS** | 音频文件存储（ASR 需要） | `TOS_ACCESS_KEY_ID`, `TOS_SECRET_ACCESS_KEY` |
| **火山引擎 TTS** | 英文语音合成 | 同豆包 credentials |
| **OpenAI** | 翻译（GPT-4o-mini） | `OPENAI_API_KEY` |
| **Gemini** | 翻译（Gemini 2.0 Flash，默认引擎） | `GEMINI_API_KEY` |
| **Demucs** | 人声分离 | 本地 |
| **FFmpeg** | 音频/视频处理 | 本地 |

---

## 7. 配置（PipelineConfig）

```python
@dataclass
class PipelineConfig:
    # ASR
    doubao_asr_preset: str = "asr_spk_semantic"
    doubao_hotwords: list[str] = ["平安", "平安哥", "于平安"]
    asr_use_vocals: bool = False

    # SUB（Utterance Normalization）
    doubao_postprofile: str = "axis"
    utt_norm_silence_split_threshold_ms: int = 450
    utt_norm_min_duration_ms: int = 900
    utt_norm_max_duration_ms: int = 8000
    utt_norm_trailing_silence_cap_ms: int = 350

    # MT
    gemini_model: str = "gemini-2.0-flash"
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.3

    # TTS
    tts_engine: str = "volcengine"
    tts_max_workers: int = 4
    tts_volume: float = 1.4
    azure_tts_language: str = "en-US"

    # MIX
    dub_target_lufs: float = -16.0
    dub_true_peak: float = -1.5
```

---

## 8. 典型工作流

```bash
# 1. 首次全流程（到 sub 暂停，检查 ASR 质量）
vsd run videos/drama/1.mp4 --to sub

# 2. 人工校准
#    - 编辑 source/asr.fix.json（修正 speaker、文本、插入遗漏台词）
#    - 编辑 dict/roles.json（分配角色声线）
#    - 编辑 dict/names.json（人名映射）

# 3. 从 sub 重跑（asr.fix.json 变更需要重跑 sub）
vsd run videos/drama/1.mp4 --from sub --to burn

# 4. 如果只改了翻译相关（names.json / slang.json），从 mt 重跑
vsd run videos/drama/1.mp4 --from mt --to burn

# 5. 批量处理
vsd run videos/drama/1-79.mp4 --to burn
```
