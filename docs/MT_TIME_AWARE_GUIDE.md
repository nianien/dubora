# 时间感知的字幕级 MT 技术方案

## 核心原则

**字幕翻译以时间轴为第一约束：每条 cue 的翻译必须满足 CPS 与最大字符限制**

- 不是翻译准不准，而是：在给定时间轴内，译文能不能被读完
- MT 阶段采用受限翻译 + 程序校验 + 二次压缩策略
- 最终结果直接写回 Subtitle Model 的 target 字段

## 工程指标

### 1. CPS（Characters Per Second）

- **英文字幕推荐**：12–17 cps
- **超过 20 cps** = 人类阅读压力明显

### 2. 最大行宽（字符数）

- **单行英文**：≤ 42 chars
- **双行合计**：≤ 84 chars

### 3. 时间窗计算

```python
duration_sec = (end_ms - start_ms) / 1000
max_chars = floor(duration_sec * cps_limit)
```

### 示例

```json
{
  "start_ms": 5280,
  "end_ms": 6580
}
```

- `duration = 1.3s`
- `cps_limit = 15`
- `max_chars ≈ 19`

翻译必须 ≤ 19 个英文字符（含空格）

## MT Prompt 设计

### 单条 cue 的推荐 Prompt

```
You are translating Chinese subtitles into English for on-screen subtitles.

Constraints:
- This subtitle will be displayed for {DURATION} seconds.
- Maximum allowed length: {MAX_CHARS} English characters (including spaces).
- The translation must be natural, concise, and readable.
- Do NOT add explanations or notes.
- Do NOT exceed the maximum length.

If the original meaning is long, prioritize clarity over completeness.

After generating the translation, silently verify that the length does not exceed {MAX_CHARS}.
If it does, rewrite it shorter.

Chinese subtitle:
"{ZH_TEXT}"

Output ONLY the English subtitle text.
```

### 示例填充

- `DURATION = 1.3`
- `MAX_CHARS = 19`
- `ZH_TEXT = 坐牢十年`

模型应输出：

```
Ten years in prison
```

（18 chars，合格）

## 翻译执行策略（两阶段）

### 阶段 A：初译（严格受限）

- 使用受限 prompt
- 得到 `candidate_1`

### 阶段 B：程序校验

```python
if len(candidate_1) <= max_chars:
    accept
else:
    retry_with_compression_prompt
```

## 二次压缩 Prompt（兜底）

如果第一次超长，使用压缩 prompt：

```
Shorten the following English subtitle to fit within {MAX_CHARS} characters,
while keeping the core meaning.

Subtitle:
"{CANDIDATE_1}"

Output ONLY the shortened subtitle.
```

通常第二次 100% 能压进来。

## 写回 SSOT 格式

在 `subtitle.model.json` 的 cue 中：

```json
{
  "cue_id": "cue_0001",
  "start_ms": 5280,
  "end_ms": 6580,
  "speaker": "spk_1",
  "source": {
    "lang": "zh",
    "text": "坐牢十年"
  },
  "target": {
    "lang": "en",
    "text": "Ten years in prison",
    "metrics": {
      "max_chars": 19,
      "actual_chars": 18,
      "cps": 13.8
    },
    "provider": "mt_v1",
    "status": "ok"
  }
}
```

这样你可以：
- 统计超限率
- 调整 cps
- 回溯 MT 质量

## 特殊情况处理

### 允许"宽松翻译"的情况

- `duration < 0.8s`：允许意译/省略修饰
- 呼喊/称呼（"哥""爸""平安哥"）：翻成 `Bro` / `Dad` / 直接省略
- 情绪词 + 重复：只保留一个核心词

## 架构流程

```
subtitle.model.json (SSOT)
        ↓
MT (cue-level, time-aware)
        ↓
更新 subtitle.model.json.cues[*].target
        ↓
render en.srt / bilingual srt / tts
```

**关键点**：
- MT 不读 zh.srt，只读 sub model
- MT 的输出直接写回 SSOT

## 配置参数

在 `config.yaml` 或环境变量中：

```yaml
phases:
  mt:
    model: "gpt-4o-mini"
    temperature: 0.3
    cps_limit: 15.0        # CPS 限制（推荐 12-17）
    max_retries: 2         # 最大重试次数
    use_time_aware: true   # 使用时间感知翻译（默认 true）
```

## 设计文档规范

**字幕翻译以时间轴为第一约束：每条 cue 的翻译必须满足 CPS 与最大字符限制；MT 阶段采用受限翻译 + 程序校验 + 二次压缩策略，最终结果直接写回 Subtitle Model 的 target 字段。**
