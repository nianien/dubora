# Subtitle Model Architecture（字幕模型架构）

## 核心原则

**Subtitle Model 是系统唯一事实源（SSOT - Single Source of Truth）**

- `asr_post.py` 是唯一可以生成 Subtitle Model 的模块
- 任何字幕文件（SRT/VTT）均为 Subtitle Model 的派生视图
- 下游模块不得反向修改 Subtitle Model 语义

---

## 系统边界

### ✅ asr_post.py 的职责

**输入**：ASR raw response（`Utterance[]`）  
**输出**：Subtitle Model（`Segment[]`）

**应该做的**：
1. **utterance → segment 转换**
   - start / end 时间轴
   - text 文本内容
   - speaker 规范化（"1" → "spk_1"）

2. **emotion 决策**
   - score < 阈值 → neutral
   - 超短句 → neutral
   - 保留 confidence / source

3. **speaker 聚合**
   - 收集 speaker 集合
   - 绑定 voice_id（或占位）

4. **文本清洗**
   - 去重标点
   - 合并"啊 / 哎 / 哥"等碎句（可选）

5. **输出 Subtitle Model**
   - JSON 格式
   - 版本稳定
   - 可回放

### ❌ asr_post.py 绝对不该做的

- ❌ 生成 .srt 文件
- ❌ 生成 .vtt 文件
- ❌ 写任何字幕文件
- ❌ 关心播放器 / 样式
- ❌ 为 TTS 直接拼 payload

**一句话**：`asr_post` 不产"文件"，只产"模型"

---

## 数据流

```
ASR raw-response.json
        │
        ▼
asr_post.py
        │   （清洗 / 归一 / 修正 / 决策）
        ▼
Subtitle Model (Segment[])  ← SSOT，唯一真相
        │
        ├── render_srt.py   →  .srt   （交付）
        ├── render_vtt.py   →  .vtt   （编辑 / QA）
        └── render_tts.py   →  TTS job（运行时）
```

---

## Subtitle Model Schema

**当前实现**：`pikppo.schema.Segment`

```python
@dataclass
class Segment:
    speaker: str              # 说话人标识（规范化后）
    start_ms: int            # 开始时间（毫秒）
    end_ms: int              # 结束时间（毫秒）
    text: str                # 文本内容（已清理）
    emotion: Optional[str]   # 情绪标签（sad/happy/neutral）
    gender: Optional[str]    # 性别标签（male/female）
```

**版本化规则**：
- Segment 结构变更必须通过版本号标识
- 旧版本 Segment 必须能自动迁移到新版本
- 迁移逻辑放在 `asr_post.py` 中

---

## 模块职责划分

### 1. `asr_post.py`
- **唯一职责**：ASR raw → Subtitle Model
- **输入**：`List[Utterance]`
- **输出**：`List[Segment]`（Subtitle Model）
- **禁止**：任何文件 IO、格式渲染

### 2. `render_srt.py`
- **唯一职责**：Subtitle Model → SRT 文件
- **输入**：`List[Segment]`
- **输出**：SRT 文件（通过 `write_srt()`）
- **职责**：格式转换、时间码格式化

### 3. `render_vtt.py`（可选）
- **唯一职责**：Subtitle Model → VTT 文件
- **输入**：`List[Segment]`
- **输出**：VTT 文件
- **职责**：WebVTT 格式转换

### 4. `render_tts.py`（可选）
- **唯一职责**：Subtitle Model → TTS job input
- **输入**：`List[Segment]`
- **输出**：TTS 任务输入（segments + voice assignment）
- **职责**：为 TTS 准备数据

### 5. `processor.py`
- **唯一职责**：Phase 层接口，调用 `asr_post` 生成 Subtitle Model
- **输入**：`List[Utterance]`
- **输出**：`ProcessorResult(data={"segments": Segment[]})`
- **禁止**：调用任何 render 函数

### 6. `sub.py` (Phase)
- **唯一职责**：文件 IO、调用 render 函数生成文件
- **输入**：`asr.result` (Utterance[])
- **输出**：`subs.zh_segments` (Subtitle Model JSON), `subs.zh_srt` (SRT 文件)
- **职责**：调用 `processor.run()` 获取 Subtitle Model，调用 `render_srt.py` 生成文件

---

## 为什么必须这么拆？

### 1. 防止"格式反噬系统"

一旦在 `asr_post` 里直接生成 SRT：
- emotion / speaker 信息丢失
- 下游再想要 → 回头改 ASR
- 系统开始绕

👉 Subtitle Model 是唯一能兜住复杂度的结构

### 2. 多个下游需求

系统至少有：
- 字幕展示（SRT）
- 字幕编辑（VTT）
- 配音（TTS）

如果 `asr_post` 直接生成 SRT/VTT：
- TTS 要反向解析字幕
- 语义不完整
- 非确定性

👉 这是典型反模式

---

## 迁移路径

### Phase 1：当前状态
- `asr_post.py` 生成 `Segment[]`
- `processor.py` 调用 `asr_post`，然后转换为 `SrtCue[]`
- `sub.py` 写入 segments.json 和 srt 文件

### Phase 2：目标状态
- `asr_post.py` 只生成 `Segment[]`（Subtitle Model）
- `render_srt.py` 负责 `Segment[]` → SRT 文件
- `processor.py` 只返回 `Segment[]`
- `sub.py` 调用 `render_srt.py` 生成文件

---

## 实施检查清单

- [ ] `asr_post.py` 移除所有文件 IO
- [ ] `asr_post.py` 移除所有格式渲染（SRT/VTT）
- [ ] 创建 `render_srt.py`（Segment[] → SRT）
- [ ] 创建 `render_vtt.py`（Segment[] → VTT，可选）
- [ ] `processor.py` 移除 `segments_to_srt_cues` 调用
- [ ] `sub.py` 调用 `render_srt.py` 生成文件
- [ ] 更新所有测试用例
