# GCP 部署手册

## 1. 架构概览

```
                    Internet
                       │
                       ▼
┌─────────────────────────────────────┐
│  dubora-web-sg (e2-micro, regional  │
│                 MIG, 常驻)          │
│  ┌─────────────────────────────────┐│
│  │ dubora-web 容器                 ││
│  │  FastAPI + React 前端           ││
│  │  Worker API (21 端点)           ││
│  └─────────────────────────────────┘│
│  Port 80 → 8765                     │
│  Volume: /var/dubora/data → /data    │
└──────────────┬──────────────────────┘
               │
               │ DB_URL
               ▼
       ┌─────────────────────┐
       │  Cloud SQL (PG)     │
       │  PostgreSQL 实例     │
       └─────────────────────┘

       ┌─────────────────────────────────┐
       │ Pipeline worker（本地 / 自托管） │
       │  vsd-pipeline worker            │
       │  --api-url=https://dub.pikppo.com│
       │  PyTorch + FFmpeg + Demucs      │
       └─────────────────────────────────┘
                 │
                 │ HTTPS → web 的 /api/worker/*
                 ▼
            (web VM)
```

- **web** 上 GCP（regional MIG，按 IAP/域名走流量），通过 `DB_URL` 连接 Cloud SQL
- **pipeline worker 不上云**，在本地（或自有机器）跑 `vsd-pipeline worker`，通过 HTTP 调 web 的 `/api/worker/*` 端点访问数据
- 视频文件通过 GCS 存取，web VM 走 service account 鉴权

## 2. 前置条件

### 2.1 GCP 项目

- 项目 ID: `pikppo`
- Web VM 区域: `asia-southeast1`（regional MIG，实例在 a/b/c zone 随机）
- Artifact Registry 仓库: `asia-east1-docker.pkg.dev/pikppo/dubora/`

### 2.2 Cloud SQL PostgreSQL

创建 Cloud SQL 实例（如果还没有）：

```bash
gcloud sql instances create dubora-pg \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=asia-southeast1 \
  --authorized-networks=0.0.0.0/0  # 生产环境应限制为 VM IP

# 创建数据库和用户
gcloud sql databases create dubora --instance=dubora-pg
gcloud sql users set-password postgres --instance=dubora-pg --password=<PASSWORD>
```

获取连接 IP 后，在 `.env` 中设置：
```
DB_URL=postgresql://postgres:<PASSWORD>@<CLOUD_SQL_IP>:5432/dubora
```

> 应用启动时会自动执行 `CREATE TABLE IF NOT EXISTS`，无需手动建表。

### 2.3 本地环境

```bash
# gcloud CLI
gcloud auth login
gcloud config set project pikppo

# 确认 .env 文件存在（含 API keys + DB_URL）
cat .env
# DB_URL=postgresql://...
# DOUBAO_APPID=...
# DOUBAO_ACCESS_TOKEN=...
# OPENAI_API_KEY=...
# GEMINI_API_KEY=...
# ...
```

### 2.4 GCS 凭证

GCS 服务账号 JSON 凭证需放在 web VM 的 `/var/dubora/data/.gcp/pikppo-dubora.json`。

```bash
# 上传凭证到 web VM（deploy-web.sh 自动处理，这里仅供手动操作）
gcloud compute scp .gcp/pikppo-dubora.json \
  nianien@<dubora-web-sg-实例名>:/var/dubora/data/.gcp/pikppo-dubora.json \
  --tunnel-through-iap
```

## 3. VM 配置

Web VM 由 regional MIG `dubora-web-sg` 管理，实例从 instance template `instance-template-cos-sg` 创建。实例名是 `dubora-web-sg-<随机后缀>`，每次 MIG 重建会变。

`deploy-web.sh` 会自动通过 `gcloud compute instances list` 解析当前 MIG 实例名，无需手动维护。

## 4. 部署命令

### 4.1 部署 Web

```bash
# 构建镜像 + 部署（默认）
bash deploy/deploy-web.sh

# 仅部署（复用已有镜像 + 重启容器）
bash deploy/deploy-web.sh --no-build

# 查看帮助
bash deploy/deploy-web.sh --help
```

Web 容器通过 `.env` 中的 `DB_URL` 连接 Cloud SQL，启动时自动建表。

### 4.2 本地跑 Pipeline Worker

Pipeline worker 在本地运行，通过 HTTPS 调用线上 web 的 Worker API：

```bash
# 默认指向 https://dub.pikppo.com
.venv/bin/vsd-pipeline worker --api-url https://dub.pikppo.com
```

### 4.3 Docker Compose（本地一体化测试）

```bash
cd deploy
docker-compose up
```

Web 在 `localhost:8765`，pipeline 自动连接 `http://web:8765`。仅用于本地端到端测试。

## 5. 镜像说明

### 5.1 dubora-web

- 基础镜像: `python:3.11-slim`
- 安装: `dubora-core` + `dubora-web`
- 前端: Node.js 构建 React → 复制到镜像
- 无 PyTorch / FFmpeg / Demucs
- 体积: ~300MB

### 5.2 Cloud Build

镜像通过 Google Cloud Build 构建，推送到 Artifact Registry：

```bash
# 手动触发（deploy 脚本内部使用）
gcloud builds submit --config=deploy/cloudbuild-web.yaml \
  --substitutions=_IMAGE_URL="asia-east1-docker.pkg.dev/pikppo/dubora/dubora-web:latest" .
```

## 6. 运维操作

### 6.1 查看日志

```bash
# Web 容器日志（VM 名通过 deploy-web.sh 的 resolve_vm 取或手动查）
VM=$(gcloud compute instances list --filter="name~^dubora-web-sg-" --format="value(name)" | head -1)
ZONE=$(gcloud compute instances list --filter="name~^dubora-web-sg-" --format="value(zone.basename())" | head -1)
gcloud compute ssh nianien@$VM --zone=$ZONE --tunnel-through-iap \
  --command="docker logs -f dubora-web --tail 100"
```

### 6.2 重启容器

```bash
gcloud compute ssh nianien@$VM --zone=$ZONE --tunnel-through-iap \
  --command="docker restart dubora-web"
```

### 6.3 进入容器调试

```bash
gcloud compute ssh nianien@$VM --zone=$ZONE --tunnel-through-iap \
  --command="docker exec -it dubora-web bash"
```

### 6.4 查看 DB

```bash
# 从本地连接 Cloud SQL（需要授权 IP 或 Cloud SQL Proxy）
psql "$DB_URL"

# 或从 web 容器内连接
gcloud compute ssh nianien@$VM --zone=$ZONE --tunnel-through-iap \
  --command="docker exec dubora-web python -c \"
from dubora_core.store import DbStore
store = DbStore()
for r in store.conn.execute('SELECT id, drama_name, number, status FROM episodes').fetchall():
    print(r)
\""
```

## 7. 环境变量（Web 容器）

| 变量 | 说明 |
|------|------|
| `DB_URL` | PostgreSQL 连接串，如 `postgresql://user:pass@host:5432/dubora` |
| `DATA_DIR` | 数据根目录，默认 `/data` |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCS 凭证路径 |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID（空则 dev 模式，无需登录） |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `AUTH_SECRET_KEY` | Cookie 签名密钥（生产环境必须设置强随机值） |
| `AUTH_ALLOWED_EMAILS` | 允许登录的邮箱白名单，逗号分隔，支持通配符（如 `*@company.com`） |
| `.env` 文件中的 API keys | 各外部服务凭证 |

> 本地 pipeline worker 走 RemoteStore (HTTP) 调线上 web 的 `/api/worker/*`，不需要 `DB_URL`。

## 8. 故障排查

### 网站无法访问

1. 检查 MIG 实例是否 RUNNING: `gcloud compute instance-groups managed describe dubora-web-sg --region=asia-southeast1`
2. 检查容器是否在运行: `docker ps`
3. 检查应用日志: `docker logs dubora-web`

### DB 连接失败

1. 确认 `.env` 中 `DB_URL` 格式正确: `postgresql://user:pass@host:5432/dbname`
2. 检查 Cloud SQL 实例是否在运行: `gcloud sql instances describe dubora-pg`
3. 检查 Cloud SQL 授权网络是否包含 web VM 的外网 IP
4. 从 web 容器内测试连接: `docker exec dubora-web python -c "from dubora_core.store import DbStore; DbStore()"`

### 封面/视频不显示

1. 检查 GCS 凭证是否正确挂载: `ls /data/.gcp/`
2. 检查环境变量: `docker exec dubora-web env | grep GOOGLE`
3. 查看 media API 日志中的 GCS 错误

### Pipeline Worker 无法连接 Web

1. 确认 `--api-url` 指向 `https://dub.pikppo.com` 或正确的内网地址
2. 从 pipeline 机器测试: `curl https://dub.pikppo.com/api/health`

### Pipeline 状态全灰

- 已完成的 episode 如果没有 task 记录（legacy 数据），pipeline 面板会自动识别为 succeeded
- 如果仍然全灰，检查 `episodes.status` 字段是否为 `"succeeded"`
