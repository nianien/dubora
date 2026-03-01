# ASR Calibration IDE - 操作手册

## 1. 概述

ASR Calibration IDE 是 Dubora 配音流水线的字幕校准工具。ASR 自动识别完成后，通过 IDE 可视化校准说话人、文本和时间轴，校准完成后一键导出，接入下游翻译和配音流程。

**适用角色**：字幕校准员、运维人员

**所处位置**：

```
demux → sep → asr → [IDE 校准] → mt → align → tts → mix → burn
                         ^
                     你在这里
```

---

## 2. 安装与启动

### 2.1 环境要求

- Python 3.9+
- Node.js 18+（仅首次构建前端需要）
- 浏览器：Chrome / Edge / Firefox

### 2.2 安装

```bash
# 安装 Python 后端依赖
make install-web

# 构建前端（仅首次或前端更新后需要）
cd web
npm install
npm run build
cd ..
```

### 2.3 启动

```bash
# 启动 IDE（默认端口 8765）
vsd ide

# 指定端口和视频目录
vsd ide --port 9000 --videos /data/videos

# 开发模式（前后端分离调试）
vsd ide --dev
```

启动后浏览器访问 **http://localhost:8765**。

---

## 3. 界面布局

```
+--------------------------------------------------------------+
|  ASR IDE  | [剧 v] [集 v] |   | Voice Casting | Save | Export |
+----------------------------+--------------------------------+
|                        | Speaker/Emotion 工具栏    |
|  视频播放器             +-------------------------+
|  (点击播放/暂停)        | 段落列表                  |
|                        | - 时间 | 说话人 | 文本    |
+------------------------+ - 点击定位播放             |
|  时间轴                 | - 双击编辑文本            |
|  (按说话人分轨)          |                          |
+------------------------+-------------------------+
|  Rev 3 | 77 segments | 12 speakers | Unsaved     |
+---------------------------------------------------+
```

| 区域 | 功能 |
|------|------|
| 顶部栏 | 剧名/集数选择、Voice Casting 入口、保存、导出 |
| 左上 | 视频播放器，点击播放/暂停 |
| 左下 | 时间轴，按说话人分轨显示段落，红色标记重叠 |
| 右上 | 说话人和情绪标注工具栏 |
| 右侧 | 段落列表，虚拟滚动，支持内联编辑 |
| 底部 | 状态栏：版本号、段落数、保存状态 |

---

## 4. 基本操作流程

### 4.1 选择剧集

1. 点击顶部下拉框，选择剧名和集数
2. 系统自动加载 ASR 结果（首次自动从 `asr-result.json` 导入）
3. 视频和段落列表同时加载

### 4.2 播放与定位

- **点击视频**：播放/暂停
- **点击段落**：跳转到该段开头播放
- **点击时间轴**：跳转到指定时间
- 播放时段落列表自动跟随高亮

### 4.3 编辑文本

- **双击**段落列表中的文本区域进入编辑
- 修改后按 **Enter** 确认，**Esc** 取消
- 编辑后底部状态栏显示 "Unsaved changes"

### 4.4 修改说话人

选中一个段落后：
- 点击右侧工具栏的说话人按钮
- 或按 **数字键 1-9** 快速切换（对应说话人列表顺序）

### 4.5 修改情绪

选中一个段落后：
- 点击右侧工具栏的情绪按钮
- 或按快捷键：**N**=neutral, **A**=angry, **S**=sad, **I**=happy, **E**=surprised, **F**=fearful

### 4.6 调整时间轴

**精细微调**（推荐）：
- 选中段落后，**Alt + 左/右箭头**：起始时间 +-50ms
- **Shift + Alt + 左/右箭头**：起始时间 +-200ms
- **Ctrl + Alt + 左/右箭头**：结束时间 +-50ms
- **Ctrl + Shift + Alt + 左/右箭头**：结束时间 +-200ms

**时间轴拖拽**：
- 选中段落后，在时间轴上拖拽段落左右边缘可直接调整起止时间

### 4.7 拆分与合并

- **Ctrl+B**（Split）：在当前播放位置拆分选中段落为两段
- **Ctrl+M**（Merge）：将选中段落与下一段合并

**操作步骤（拆分）**：
1. 选中目标段落
2. 播放视频，在需要拆分的位置暂停
3. 按 Ctrl+B，段落在播放头位置一分为二

### 4.8 删除段落

- 选中段落后按 **Delete** 或 **Backspace**

### 4.9 保存

- 按 **Ctrl+S** 或点击顶部 Save 按钮
- 保存后版本号 +1，指纹自动更新
- 文件保存到 `source/asr.model.json`

### 4.10 导出

点击顶部 **Export** 按钮，自动生成三个文件：

| 文件 | 路径 | 用途 |
|------|------|------|
| `subtitle.model.json` | `source/` | 下游 MT/Align 阶段的输入（SSOT） |
| `asr.fix.json` | `source/` | 向后兼容旧流程 |
| `zh.srt` | `render/` | 中文字幕文件 |

导出完成后，可执行下游 pipeline：

```bash
vsd run videos/剧名/集号.mp4 --from mt --to burn
```

---

## 5. 快捷键速查表

### 通用（任何时候可用）

| 快捷键 | 功能 |
|--------|------|
| Ctrl+S | 保存 |
| Ctrl+Z | 撤销 |
| Ctrl+Shift+Z | 重做 |

### 非编辑状态（文本框未获焦时）

| 快捷键 | 功能 |
|--------|------|
| Space | 播放 / 暂停 |
| Enter | 跳到下一段并播放 |
| 上/下箭头 | 上一段 / 下一段 |
| 1-9 | 切换说话人 |
| N / A / S / I / E / F | 切换情绪 |
| Alt + 左/右 | 起始时间 +-50ms |
| Shift+Alt + 左/右 | 起始时间 +-200ms |
| Ctrl+Alt + 左/右 | 结束时间 +-50ms |
| Ctrl+B | 拆分段落（在播放头位置） |
| Ctrl+M | 合并段落（与下一段） |
| Delete | 删除选中段落 |

### 文本编辑中

| 快捷键 | 功能 |
|--------|------|
| Enter | 确认编辑 |
| Esc | 取消编辑 |

---

## 6. 时间轴操作

### 6.1 缩放

- 按住 **Ctrl**（Mac: Cmd）+ **鼠标滚轮**：放大/缩小时间轴

### 6.2 滚动

- **鼠标滚轮**：左右滚动时间轴

### 6.3 颜色含义

| 颜色 | 含义 |
|------|------|
| 各色条 | 不同说话人（蓝、绿、紫、橙等） |
| 白色边框 | 当前选中段落 |
| 红色半透明 | 时间重叠（overlap） |
| 红色竖线 | 当前播放位置 |

---

## 7. 典型工作流程

### 7.1 新集校准

```bash
# 1. 先跑 ASR
vsd run videos/drama/1.mp4 --to asr

# 2. 启动 IDE
vsd ide --videos ./videos

# 3. 浏览器打开 http://localhost:8765
#    选择剧集 → 自动加载 ASR 结果

# 4. 校准工作：
#    - 逐段检查文本，双击修正错别字
#    - 用 1-9 快速修正说话人
#    - 拆分/合并错误分段
#    - Ctrl+S 保存

# 5. 导出
#    点击 Export → 生成 subtitle.model.json

# 6. 继续 pipeline
vsd run videos/drama/1.mp4 --from mt --to burn
```

### 7.2 批量校准

```bash
# 1. 批量跑 ASR
vsd run videos/drama/1-20.mp4 --to asr

# 2. 启动 IDE，逐集校准
vsd ide --videos ./videos

# 3. 每集校准完导出后，批量跑后续
vsd run videos/drama/1-20.mp4 --from mt --to burn
```

### 7.3 返工修正

如果导出后发现仍需修改：

1. 回到 IDE 修改
2. Ctrl+S 保存
3. 再次 Export
4. 重跑 `vsd run --from mt --to burn`

---

## 8. 文件说明

| 文件 | 位置 | 说明 |
|------|------|------|
| `asr-result.json` | `source/` | ASR 原始输出（只读，不要手动修改） |
| `asr.model.json` | `source/` | IDE 工作文件（保存/加载用，可视为草稿） |
| `subtitle.model.json` | `source/` | 导出产物，下游 pipeline 的输入 |
| `asr.fix.json` | `source/` | 导出产物，向后兼容旧流程 |
| `zh.srt` | `render/` | 导出产物，中文字幕 |

**注意**：
- `asr.model.json` 是 IDE 的工作文件，每次保存自动更新版本号和指纹
- 只有点击 **Export** 后才会生成 `subtitle.model.json`，才能被下游 pipeline 使用
- 不要手动编辑 `asr.model.json`，请通过 IDE 操作

---

## 9. Voice Casting（声线分配）

Voice Casting 是独立于 ASR 校准的全屏视图，用于管理 `roles.json`——将角色绑定到 TTS 音色。

### 9.1 进入 / 退出

- 点击主界面右上角 **Voice Casting** 按钮进入
- 进入后主界面完全切换（隐藏剧集选择和 ASR 相关控件）
- 点击左上角 **← Back** 返回 ASR IDE

### 9.2 界面布局

```
+--------------------------------------------------------------+
|  ← Back | Voice Casting | [剧名 v] |           | Save        |
+--------------------------------------------------------------+
|  ▶ Playing: Glen (trial)  [====audio====]  Download           |
+-------------------+------------------------------------------+
| Roles             | Voice Catalogue                           |
|                   | Category: [All] [有声书] ...  Gender: ... |
| ● PingAn          |                                           |
|   Glen            | ○ ▶ Candice  女/青年  happy neutral  [Try] |
| ○ BaiYe           | ● ▶ Glen     男/青年  happy neutral  [Try] |
|   (none)          |    ┌─ inline synthesis ──────────────┐    |
|                   |    │ Emotion [▼] Text [___] [Synth]  │    |
| Default Roles     |    │ ▶ happy "I never thought..."    │    |
| ○ male            |    └─────────────────────────────────┘    |
|   Glen            | ○ ▶ Sylus    男/青年  happy neutral  [Try] |
| ○ female          |                                           |
|   (none)          |                                           |
+-------------------+------------------------------------------+
```

| 区域 | 说明 |
|------|------|
| Header | 独立 header，含剧名下拉框（只选剧、不选集）、Save 按钮 |
| 播放条 | 全局音频播放器，试听 / 合成结果共用 |
| 左栏 | 角色列表（`roles` + `default_roles`），蓝色高亮选中角色 |
| 右栏 | 音色目录，支持 Category / Gender 筛选 |

### 9.3 操作流程

#### 分配音色

1. 顶部下拉框选择**剧名**
2. 左栏点击角色（如 "PingAn"），进入分配模式
3. 右栏浏览音色，点击 ▶ 试听官方 trial
4. 点击目标音色卡片 → 该音色自动分配给当前角色（蓝色圆点标记）
5. 点击 **Save** 写回 `roles.json`

**自动滚动**：如果被选中的角色已有绑定音色，右栏会自动滚动到该音色卡片并居中显示。

#### 自定义合成试听

1. 点击音色卡片右侧的 **Try** 按钮 → 展开内联合成面板
2. 选择 **Emotion**（从该音色支持的情绪列表中选择）
3. 输入试听文本（有默认文本，可自定义）
4. 点击 **Synthesize** → 合成完成后自动播放
5. 面板下方显示该音色的**合成历史**，点击 ▶ 可回放
6. 再次点击 **Try** 收起面板；展开另一个音色时上一个自动收起

### 9.4 注意事项

- `roles.json` 是**剧级别**配置，所有集共享同一份声线映射
- Voice Casting 不涉及剧集选择，只需选择剧名
- 合成试听会调用 VolcEngine TTS API，需要配置 `DOUBAO_APPID` / `DOUBAO_ACCESS_TOKEN`
- 合成结果缓存在 `.cache/voice-preview/`，相同 voice + text + emotion 不会重复调用 API

---

## 10. 常见问题

### Q: 打开 IDE 后看不到任何剧集？

检查 `--videos` 参数指向的目录结构是否正确：
```
videos/
  剧名/
    1.mp4
    dub/
      1/
        source/
          asr-result.json
```

### Q: 选择剧集后报 "asr-result.json not found"？

该集尚未运行 ASR 阶段，先执行：
```bash
vsd run videos/剧名/集号.mp4 --to asr
```

### Q: 视频播放不了？

- 确认视频文件位于 `videos/剧名/集号.mp4`（与 dub 目录同级）
- 浏览器需支持 H.264 编码

### Q: 保存后如何确认已保存成功？

查看底部状态栏：
- "Unsaved changes"（黄色）= 有未保存的修改
- "Saved"（绿色）= 已保存
- Rev 数字会 +1

### Q: Export 后如何触发下游 pipeline？

```bash
vsd run videos/剧名/集号.mp4 --from mt --to burn
```

### Q: 如何回退到 ASR 原始结果？

删除 `source/asr.model.json`，重新在 IDE 中打开该集，会自动从 `asr-result.json` 重新导入。

---

## 11. 运维信息

### 11.1 服务端口

| 服务 | 默认端口 | 说明 |
|------|---------|------|
| IDE 后端 | 8765 | FastAPI + Uvicorn |
| Vite Dev | 5173 | 仅开发模式 |

### 11.2 依赖项

```bash
# 查看已安装的 web 依赖
pip show fastapi uvicorn
```

### 11.3 日志

IDE 启动后日志输出在终端，包含请求日志和错误信息。

### 11.4 数据安全

- 所有修改通过原子写入（先写临时文件再重命名），断电不会损坏文件
- 每次保存自动递增版本号（rev），可追溯修改历史
- 原始 `asr-result.json` 永远不会被修改
