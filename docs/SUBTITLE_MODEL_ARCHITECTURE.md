# Subtitle Model Architecture（字幕模型架构）v1.1

## 核心原则

**Subtitle Model v1.1 是系统的唯一事实源（SSOT）**

- `subtitle.model.json` 是 SSOT v1.1，极简设计，只保留必要字段
- `zh.srt` 是 Subtitle Model 的**格式视图**（用于播放器）
- 所有字幕文件（SRT/VTT）都从 Subtitle Model 派生

## 极简 SSOT 设计原则

- **SSOT**：唯一真相，后续阶段都只读/只补充自己字段
- **最小字段集**：只保留会被下游用到的
- **结构明确**：source/target 分离，避免未来混乱
- **不把 raw/additions 塞进模型**：那是 ASR 原始事实，不是 SSOT

## 架构分层

```
ASR raw-response.json (SSOT) ✅
        │
        ▼
parse_utterances() (models/doubao/parser.py)
        │   （解析为 Utterance[]）
        ▼
asr_post.py
        │   （清洗 / 归一 / 修正 / 决策）
        ▼
Segment[] (中间态)
        │
        ▼
build_subtitle_model.py
        │   （规范化 speaker / 保留完整语义）
        ▼
Subtitle Model v1.1 (SSOT) ✅
        │
        ├── render_srt.py              →  zh.srt             （播放器）
        └── render_vtt.py              →  zh.vtt             （编辑器/QA）
```

**关键设计决策**：
- **raw-response.json 是事实源**：直接从 raw-response 生成 Subtitle Model，不经过中间 result.json
- **result.json 已移除**：不再生成中间态的 result.json，减少重复与语义漂移

## 文件定位

| 文件 | 定位 | 用途 |
|------|------|------|
| `subtitle.model.json` | ✅ **SSOT v1.1** | 系统唯一事实源，极简设计，只保留必要字段 |
| `zh.srt` | ❌ **视图** | SRT 格式，用于播放器 |
| `zh.vtt` | ❌ **视图** | VTT 格式，用于编辑器/QA |

## Subtitle Model v1.1 结构

```json
{
  "schema": {
    "name": "subtitle.model",
    "version": "1.1"
  },
  "audio": {
    "duration_ms": 149884
  },
  "speakers": {
    "spk_1": {
      "speaker_id": "spk_1",
      "voice_id": null
    },
    "spk_2": {
      "speaker_id": "spk_2",
      "voice_id": null
    }
  },
  "cues": [
    {
      "cue_id": "cue_0001",
      "start_ms": 5280,
      "end_ms": 6580,
      "speaker": "spk_1",
      "source": {
        "lang": "zh",
        "text": "坐牢十年"
      },
      "target": null,
      "emotion": {
        "label": "sad",
        "confidence": 0.88,
        "intensity": "weak"
      }
    }
  ]
}
```

## 各阶段职责（ownership 清晰）

### asr_post 阶段
- **写**：speakers、cues[*].source、start/end/speaker、emotion(可选)
- **不写**：target（由 MT 阶段填写）、voice_id（由 TTS 阶段分配）

### MT 阶段
- **只写**：cues[*].target
- **不写**：时间轴、说话人、emotion（只读）

### TTS 阶段
- **不写**：SSOT（只读生成 tts_jobs）
- **读取**：voice_id + target/source + emotion

## 模块职责

### `asr_post.py`
- **职责**：ASR raw → Segment[]（中间态）
- **不做**：不生成任何文件，不输出 Subtitle Model

### `build_subtitle_model.py`
- **职责**：Segment[] → Subtitle Model v1.1 (SSOT)
- **功能**：
  - 规范化 speaker ID（"1" → "spk_1"）
  - 保留完整的 emotion 语义（不丢失 confidence/intensity）
  - 构建 speakers 实体定义（最小必需字段：speaker_id, voice_id）
  - 构建 source/target 分离结构

### `render_srt.py`
- **职责**：Subtitle Model v1.1 → zh.srt（格式视图）
- **用途**：播放器显示（使用 source.text）

### `render_vtt.py`（未来）
- **职责**：Subtitle Model v1.1 → zh.vtt（格式视图）
- **用途**：编辑器/QA

## 关键设计决策

1. **Subtitle Model 是 SSOT**：所有字幕文件都从这里派生，不反向修改
2. **保留完整 emotion 语义**：emotion 包含 confidence/intensity，不丢失信息
3. **规范化 speaker ID**：统一使用 "spk_1" 格式，便于后续 TTS 分配 voice_id
4. **source/target 分离**：明确语言归属，避免未来混乱
5. **最小字段集**：只保留会被下游用到的字段，删除 gender/profile/additions

## 一句话总结

**Subtitle Model v1.1 是系统唯一事实源，极简设计，只保留必要字段。所有字幕文件均为其派生视图。**
