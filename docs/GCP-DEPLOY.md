# GCP 部署手册

## 1. 架构概览

```
                    Internet
                       │
                       ▼
┌─────────────────────────────────────┐
│  dubora-web-sg (e2-small, 常驻)     │
│  ┌─────────────────────────────────┐│
│  │ dubora-web 容器                 ││
│  │  FastAPI + React 前端           ││
│  │  Worker API (21 端点)           ││
│  │  SQLite DB (/data/db/)          ││
│  └─────────────────────────────────┘│
│  Port 80 → 8765                     │
│  Volume: /var/dubora/data → /data    │
└──────────────┬──────────────────────┘
               │ Internal IP (HTTP)
               ▼
┌─────────────────────────────────────┐
│  dubora-pipeline-sg (GPU/高CPU, 按需)│
│  ┌─────────────────────────────────┐│
│  │ dubora-pipeline 容器            ││
│  │  PipelineWorker (远程模式)      ││
│  │  RemoteStore → Worker API       ││
│  │  PyTorch + FFmpeg + Demucs      ││
│  └─────────────────────────────────┘│
│  Volume: /var/dubora/data → /data    │
└─────────────────────────────────────┘
```

- **web** 跑在便宜机器，拥有 SQLite DB，暴露 Worker API
- **pipeline** 跑在贵机器（GPU/高 CPU），通过 HTTP API 读写数据库
- 视频文件通过 GCS 存取，两台机器都挂载 GCS 凭证

## 2. 前置条件

### 2.1 GCP 项目

- 项目 ID: `pikppo`
- 区域: `asia-southeast1-a`
- Artifact Registry 仓库: `asia-east1-docker.pkg.dev/pikppo/dubora/`

### 2.2 本地环境

```bash
# gcloud CLI
gcloud auth login
gcloud config set project pikppo

# 确认 .env 文件存在（含 API keys）
cat .env
# DOUBAO_APPID=...
# DOUBAO_ACCESS_TOKEN=...
# OPENAI_API_KEY=...
# GEMINI_API_KEY=...
# ...
```

### 2.3 GCS 凭证

GCS 服务账号 JSON 凭证需放在 VM 的 `/var/dubora/data/.gcp/pikppo-dubora.json`。

```bash
# 上传凭证到 web VM
gcloud compute scp .gcp/pikppo-dubora.json \
  nianien@dubora-web-sg:/var/dubora/data/.gcp/pikppo-dubora.json \
  --zone=asia-southeast1-a

# 上传凭证到 pipeline VM（如果需要）
gcloud compute scp .gcp/pikppo-dubora.json \
  nianien@dubora-pipeline-sg:/var/dubora/data/.gcp/pikppo-dubora.json \
  --zone=asia-southeast1-a
```

## 3. VM 创建（首次）

### 3.1 Web VM

```bash
gcloud compute instances create dubora-web-sg \
  --zone=asia-southeast1-a \
  --machine-type=e2-small \
  --tags=http-server \
  --image-family=cos-stable \
  --image-project=cos-cloud \
  --boot-disk-size=20GB

# 挂载数据盘（如需独立磁盘）
# gcloud compute disks create dubora-data --size=50GB --zone=asia-southeast1-a
# gcloud compute instances attach-disk dubora-web-sg --disk=dubora-data --zone=asia-southeast1-a
```

确保防火墙规则允许 HTTP 流量：
```bash
# 如果 VM 没有 http-server 标签
gcloud compute instances add-tags dubora-web-sg --tags=http-server --zone=asia-southeast1-a
```

### 3.2 Pipeline VM

```bash
gcloud compute instances create dubora-pipeline-sg \
  --zone=asia-southeast1-a \
  --machine-type=n1-standard-4 \
  --image-family=cos-stable \
  --image-project=cos-cloud \
  --boot-disk-size=50GB
```

### 3.3 数据目录初始化

在每台 VM 上创建数据目录：
```bash
gcloud compute ssh nianien@dubora-web-sg --zone=asia-southeast1-a --command="
  sudo mkdir -p /var/dubora/data/{db,.gcp}
  sudo chown -R \$(id -u):\$(id -g) /var/dubora/data
"
```

## 4. 部署命令

### 4.1 部署 Web

```bash
# 仅部署（使用已有镜像）
bash deploy/deploy-web.sh

# 构建镜像 + 部署
bash deploy/deploy-web.sh --build

# 构建 + 同步本地 DB + 部署
bash deploy/deploy-web.sh --build --init

# 查看帮助
bash deploy/deploy-web.sh --help
```

`--init` 会将本地 `data/db/dubora.db` 上传到 VM，适用于首次部署或 DB 重置。

### 4.2 部署 Pipeline

```bash
# 仅部署（使用已有镜像）
bash deploy/deploy-pipeline.sh

# 构建镜像 + 部署
bash deploy/deploy-pipeline.sh --build

# 查看帮助
bash deploy/deploy-pipeline.sh --help
```

Pipeline 部署脚本会自动解析 web VM 的内网 IP，设置 `API_URL` 环境变量。

### 4.3 Docker Compose（本地测试）

```bash
cd deploy
docker-compose up
```

Web 在 `localhost:8765`，pipeline 自动连接 `http://web:8765`。

## 5. 镜像说明

### 5.1 dubora-web

- 基础镜像: `python:3.11-slim`
- 安装: `dubora-core` + `dubora-web`
- 前端: Node.js 构建 React → 复制到镜像
- 无 PyTorch / FFmpeg / Demucs
- 体积: ~300MB

### 5.2 dubora-pipeline

- 基础镜像: `python:3.11-slim`
- 安装: `dubora-core` + `dubora-pipeline`（含 PyTorch CPU + FFmpeg）
- 无前端
- 体积: ~2GB

### 5.3 Cloud Build

镜像通过 Google Cloud Build 构建，推送到 Artifact Registry：

```bash
# 手动触发（deploy 脚本内部使用）
gcloud builds submit --config=deploy/cloudbuild-web.yaml \
  --substitutions=_IMAGE_URL="asia-east1-docker.pkg.dev/pikppo/dubora/dubora-web:latest" .
```

## 6. 运维操作

### 6.1 查看日志

```bash
# Web 容器日志
gcloud compute ssh nianien@dubora-web-sg --zone=asia-southeast1-a \
  --command="docker logs -f dubora-web --tail 100"

# Pipeline 容器日志
gcloud compute ssh nianien@dubora-pipeline-sg --zone=asia-southeast1-a \
  --command="docker logs -f dubora-pipeline --tail 100"
```

### 6.2 重启容器

```bash
gcloud compute ssh nianien@dubora-web-sg --zone=asia-southeast1-a \
  --command="docker restart dubora-web"
```

### 6.3 进入容器调试

```bash
gcloud compute ssh nianien@dubora-web-sg --zone=asia-southeast1-a \
  --command="docker exec -it dubora-web bash"
```

### 6.4 查看 DB

```bash
gcloud compute ssh nianien@dubora-web-sg --zone=asia-southeast1-a \
  --command="docker exec dubora-web python -c \"
import sqlite3
conn = sqlite3.connect('/data/db/dubora.db')
for r in conn.execute('SELECT id, drama_name, number, status FROM episodes'):
    print(r)
\""
```

### 6.5 停止 Pipeline VM（省钱）

Pipeline VM 按需启动，不用时可停止：

```bash
# 停止
gcloud compute instances stop dubora-pipeline-sg --zone=asia-southeast1-a

# 启动
gcloud compute instances start dubora-pipeline-sg --zone=asia-southeast1-a
# 启动后需要重新部署容器（COS VM 重启后 docker 状态可能丢失）
bash deploy/deploy-pipeline.sh
```

## 7. 环境变量

### Web 容器

| 变量 | 说明 |
|------|------|
| `DATA_DIR` | 数据根目录，默认 `/data` |
| `DB_DIR` | DB 目录，默认 `DATA_DIR/db`（可独立覆盖） |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCS 凭证路径 |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID（空则 dev 模式，无需登录） |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `AUTH_SECRET_KEY` | Cookie 签名密钥（生产环境必须设置强随机值） |
| `AUTH_ALLOWED_EMAILS` | 允许登录的邮箱白名单，逗号分隔，支持通配符（如 `*@company.com`） |
| `.env` 文件中的 API keys | 各外部服务凭证 |

### Pipeline 容器

| 变量 | 说明 |
|------|------|
| `DATA_DIR` | 数据根目录，默认 `/data` |
| `API_URL` | Web API 地址，如 `http://10.148.0.2:8765` |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCS 凭证路径 |
| `.env` 文件中的 API keys | 各外部服务凭证 |

## 8. 故障排查

### 网站无法访问

1. 检查 VM 是否有 `http-server` 网络标签
2. 检查容器是否在运行: `docker ps`
3. 检查应用日志: `docker logs dubora-web`

### 封面/视频不显示

1. 检查 GCS 凭证是否正确挂载: `ls /data/.gcp/`
2. 检查环境变量: `docker exec dubora-web env | grep GOOGLE`
3. 查看 media API 日志中的 GCS 错误

### Pipeline Worker 无法连接 Web

1. 确认 web VM 内网 IP: `gcloud compute instances describe dubora-web-sg --zone=asia-southeast1-a --format="get(networkInterfaces[0].networkIP)"`
2. 检查 `API_URL` 环境变量: `docker exec dubora-pipeline env | grep API_URL`
3. 从 pipeline VM 测试连通性: `curl http://<web-ip>:8765/api/health`

### Pipeline 状态全灰

- 已完成的 episode 如果没有 task 记录（legacy 数据），pipeline 面板会自动识别为 succeeded
- 如果仍然全灰，检查 `episodes.status` 字段是否为 `"succeeded"`
