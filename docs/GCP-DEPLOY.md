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
               │ DB_URL（postgresql://...sslmode=require）
               ▼
       ┌──────────────────────────────────┐
       │  Neon Serverless Postgres        │
       │  ep-young-sea-a1b6u97h-pooler    │
       │  .ap-southeast-1.aws.neon.tech   │
       └──────────────────────────────────┘

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

- **web** 上 GCP（regional MIG，按 IAP/域名走流量），通过 `DB_URL` 连接 Neon serverless Postgres
- **pipeline worker 不上云**，在本地（或自有机器）跑 `vsd-pipeline worker`，通过 HTTP 调 web 的 `/api/worker/*` 端点访问数据
- 视频文件通过 GCS 存取，web VM 走 service account 鉴权

## 2. 前置条件

### 2.1 GCP 项目

- 项目 ID: `pikppo`
- Web VM 区域: `asia-southeast1`（regional MIG，实例在 a/b/c zone 随机）
- Artifact Registry 仓库: `asia-east1-docker.pkg.dev/pikppo/dubora/`

### 2.2 数据库（Neon Serverless Postgres）

DB 用 [Neon](https://neon.tech)（serverless Postgres，免运维，按用量计费），不是 GCP Cloud SQL。

- Project: `pikppo`
- Region: `ap-southeast-1`（AWS Singapore；跟 web VM 的 `asia-southeast1` 同地区，避开跨区延迟）
- Host: `ep-young-sea-a1b6u97h-pooler.ap-southeast-1.aws.neon.tech`
- Pooler endpoint：连接走 PgBouncer pooled connection，serverless cold-start 后第一次查询会重连一次（应用层 `_execute` 已含 `psycopg2.OperationalError` 自动重连兜底）

在 `.env` 中设置：
```
DB_URL=postgresql://<user>:<password>@ep-young-sea-a1b6u97h-pooler.ap-southeast-1.aws.neon.tech/pikppo?sslmode=require&channel_binding=require
```

> 应用启动时会自动执行 `CREATE TABLE IF NOT EXISTS`，无需手动建表。
> Neon 后台一键开 branch、备份、扩 storage，运维比 Cloud SQL 轻量得多。

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

### 2.4 GCS 鉴权（走 Application Default Credentials）

不再使用 JSON service account key 文件。所有环境统一走 ADC：

- **GCP VM**：instance template 绑定 service account（当前 `dubora@pikppo.iam.gserviceaccount.com`，scope `cloud-platform`），容器内 `storage.Client()` 自动通过 metadata server 拿 token。
- **本地开发**：`gcloud auth application-default login` 一次，凭据写到 `~/.config/gcloud/application_default_credentials.json`，应用自动读。
- **CI / 自托管**：推荐 [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation)；不得已用 SA key 文件时通过 `GOOGLE_APPLICATION_CREDENTIALS` 指向，注意定期轮换并存 secret manager。

> Instance template `instance-template-cos-sg-v2` 已绑 `dubora@pikppo`，无需手动操作。`deploy-web.sh` 不再上传 JSON key 到 VM。

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

Web 容器通过 `.env` 中的 `DB_URL` 连接 Neon Postgres，启动时自动建表。

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
# 从本地直接连 Neon（DB_URL 内置 sslmode=require + 凭据，无需额外授权）
psql "$DB_URL"

# Neon Console: https://console.neon.tech/app/projects → 项目 pikppo

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
| `GCS_BUCKET` | GCS 桶名，默认 `dubora`。鉴权走 ADC（VM 关联 SA / 本地 gcloud login），**不再使用 JSON key 文件** |
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

1. 确认 `.env` 中 `DB_URL` 格式正确: `postgresql://user:pass@host/dbname?sslmode=require`
2. 检查 Neon 项目状态：[Neon Console](https://console.neon.tech) → pikppo → 确认 endpoint 是 Active（serverless 闲置会自动 suspend，首次请求会 cold-start 几秒）
3. 应用层 `DbStore._execute` 已自动重试 `OperationalError` / `InterfaceError`（含 Neon SSL EOF），偶发抖动会自愈
4. 从 web 容器内测试连接: `docker exec dubora-web python -c "from dubora_core.store import DbStore; DbStore('${DB_URL}')"`

### 封面/视频不显示

1. 检查 VM 关联的 SA：`gcloud compute instances describe <vm> --zone=<zone> --format="value(serviceAccounts[0].email)"`，应是 `dubora@pikppo.iam.gserviceaccount.com`
2. 容器内验证 metadata token 能拿到：
   ```
   docker exec dubora-web python -c "from google.cloud import storage; print(storage.Client().bucket('dubora').exists())"
   ```
3. 查看 media API 日志中的 GCS 错误（401 / 403 通常是 SA 权限问题）

### Pipeline Worker 无法连接 Web

1. 确认 `--api-url` 指向 `https://dub.pikppo.com` 或正确的内网地址
2. 从 pipeline 机器测试: `curl https://dub.pikppo.com/api/health`

### Pipeline 状态全灰

- 已完成的 episode 如果没有 task 记录（legacy 数据），pipeline 面板会自动识别为 succeeded
- 如果仍然全灰，检查 `episodes.status` 字段是否为 `"succeeded"`
