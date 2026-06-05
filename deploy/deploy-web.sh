#!/bin/bash
# 部署 web 服务到 GCP COS VM
# Usage: bash deploy/deploy-web.sh [--no-build]
#   无参数      构建新镜像 + 部署（默认）
#   --no-build  仅部署容器（复用已有镜像 + 上传 .env + 重启）
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────
PROJECT="pikppo"
REGION="asia-east1"
MIG_REGION="asia-southeast1"   # web MIG 所在 region（regional MIG）
MIG_NAME="dubora-web-sg"        # regional MIG 名（实例名 = ${MIG_NAME}-xxxx 随机后缀）
REPO="dubora"
IMAGE="dubora-web"
IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest"

VM_USER="${VM_USER:-$USER}"   # 默认用本地用户名（gcloud SSH 在 VM 上会建对应账户）
CONTAINER_NAME="dubora-web"
DATA_DIR="/var/dubora/data"
PORT="8765"

# 运行时由 resolve_vm() 填充
VM_NAME=""
ZONE=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── 工具 ──────────────────────────────────────────────────
# 通过 IAP tunnel 走 Google 代理，避免出口 IP 被 sshd 拒绝
log()  { echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }
vm_ssh() { gcloud compute ssh "${VM_USER}@${VM_NAME}" --zone="$ZONE" --tunnel-through-iap --command="$1"; }
vm_scp() { gcloud compute scp "$1" "${VM_USER}@${VM_NAME}:$2" --zone="$ZONE" --tunnel-through-iap; }

# ── 前置检查 ──────────────────────────────────────────────
check_prerequisites() {
    command -v gcloud >/dev/null || fail "gcloud CLI not installed"
    if ! gcloud auth print-access-token &>/dev/null; then
        log "No active gcloud account, launching login..."
        gcloud auth login
    fi
    gcloud config set project "$PROJECT" --quiet
    [ -f "$PROJECT_DIR/.env" ] || fail ".env not found"
}

# ── 从 MIG 查当前实例名 + zone（实例名后缀随机，每次重建会变）─────
resolve_vm() {
    log "Resolving VM from MIG '${MIG_NAME}'..."
    local line
    line=$(gcloud compute instances list \
        --filter="name~^${MIG_NAME}-" \
        --format="value(name,zone.basename())" | head -n1)
    VM_NAME=$(echo "$line" | awk '{print $1}')
    ZONE=$(echo "$line" | awk '{print $2}')
    [ -n "$VM_NAME" ] || fail "No instance found matching ^${MIG_NAME}-"
    log "VM: ${VM_NAME} (zone=${ZONE})"
}

# ── 构建镜像 ─────────────────────────────────────────────
build_image() {
    log "Building web image via Cloud Build..."
    cd "$PROJECT_DIR"
    gcloud builds submit --config=deploy/cloudbuild-web.yaml --substitutions=_IMAGE_URL="$IMAGE_URL" .
}

# ── 部署容器 ─────────────────────────────────────────────
deploy_to_vm() {
    log "Uploading .env..."
    vm_scp "$PROJECT_DIR/.env" "~/.env.dubora"

    log "Preparing data directories..."
    vm_ssh "
        sudo mkdir -p ${DATA_DIR}/.gcp
        sudo chown -R \$(id -u):\$(id -g) ${DATA_DIR%/*}
    "

    log "Uploading GCP service account key..."
    [ -f "$PROJECT_DIR/.gcp/pikppo-dubora.json" ] || fail ".gcp/pikppo-dubora.json not found"
    vm_scp "$PROJECT_DIR/.gcp/pikppo-dubora.json" "${DATA_DIR}/.gcp/pikppo-dubora.json"

    log "Deploying container..."
    log "Authenticating Docker on VM..."
    local token
    token=$(gcloud auth print-access-token)
    vm_ssh "echo '${token}' | docker login -u oauth2accesstoken --password-stdin https://${REGION}-docker.pkg.dev"

    vm_ssh "
        docker pull ${IMAGE_URL}
        docker rm -f ${CONTAINER_NAME} 2>/dev/null || true
        docker run -d \
            --name ${CONTAINER_NAME} \
            --restart unless-stopped \
            -p 80:${PORT} \
            -v ${DATA_DIR}:/data \
            --env-file ~/.env.dubora \
            -e GOOGLE_APPLICATION_CREDENTIALS=/data/.gcp/pikppo-dubora.json \
            ${IMAGE_URL}
        docker ps --filter name=${CONTAINER_NAME}
    "

    EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
        --zone="$ZONE" --format="get(networkInterfaces[0].accessConfigs[0].natIP)")
    log "Done!  http://${EXTERNAL_IP}  |  https://dub.pikppo.com"
}

# ── 用法 ────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: bash deploy/deploy-web.sh [OPTIONS]

Deploy dubora-web to GCP VM.
DB is PostgreSQL (Cloud SQL), configured via DB_URL in .env.

Options:
  --no-build  Skip image build; pull existing image + restart container
  --help      Show this help

Without options: build new image via Cloud Build, then deploy.

Examples:
  bash deploy/deploy-web.sh                # Build + deploy (default)
  bash deploy/deploy-web.sh --no-build     # Deploy only (reuse last image)
EOF
    exit 0
}

# ── 主流程 ────────────────────────────────────────────────
BUILD=true
for arg in "$@"; do
    case "$arg" in
        --no-build) BUILD=false ;;
        --help|-h)  usage ;;
        *)          fail "Unknown argument: $arg" ;;
    esac
done

check_prerequisites
if $BUILD; then build_image; fi
resolve_vm
deploy_to_vm
