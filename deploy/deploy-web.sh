#!/bin/bash
# 部署 web 服务到 GCP COS VM
# Usage: bash deploy/deploy-web.sh [--build] [--init]
#   --build  构建新镜像（不传则复用已有镜像）
#   --init   同步本地 DB 到 VM
#   无参数    仅部署容器（拉取已有镜像 + 上传 .env + 重启）
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────
PROJECT="pikppo"
REGION="asia-east1"
ZONE="asia-southeast1-a"
REPO="dubora"
IMAGE="dubora-web"
IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest"

VM_NAME="sg-dubora-web"
VM_USER="nianien_gmail_com"
CONTAINER_NAME="dubora-web"
DATA_DIR="/mnt/disks/data"
PORT="8765"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── 工具 ──────────────────────────────────────────────────
log()  { echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }
vm_ssh() { gcloud compute ssh "${VM_USER}@${VM_NAME}" --zone="$ZONE" --command="$1"; }
vm_scp() { gcloud compute scp "$1" "${VM_USER}@${VM_NAME}:$2" --zone="$ZONE"; }

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

# ── 同步本地 DB 到 VM ────────────────────────────────────
init_data() {
    LOCAL_DB="$PROJECT_DIR/data/db/dubora.db"

    if [ ! -f "$LOCAL_DB" ]; then
        fail "Local DB not found: $LOCAL_DB"
    fi

    log "Stopping container before data sync..."
    vm_ssh "docker rm -f ${CONTAINER_NAME} 2>/dev/null || true"

    log "Uploading dubora.db to VM..."
    vm_scp "$LOCAL_DB" "${DATA_DIR}/db/dubora.db"

    log "DB synced."
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

    log "Deploying container..."
    vm_ssh "
        docker pull ${IMAGE_URL}
        docker rm -f ${CONTAINER_NAME} 2>/dev/null || true
        docker run -d \
            --name ${CONTAINER_NAME} \
            --restart unless-stopped \
            -p 80:${PORT} \
            -v ${DATA_DIR}:/data \
            --env-file ~/.env.dubora \
            ${IMAGE_URL}
        docker ps --filter name=${CONTAINER_NAME}
    "

    EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
        --zone="$ZONE" --format="get(networkInterfaces[0].accessConfigs[0].natIP)")
    log "Done!  http://${EXTERNAL_IP}"
}

# ── 主流程 ────────────────────────────────────────────────
BUILD=false
INIT=false
for arg in "$@"; do
    case "$arg" in
        --build) BUILD=true ;;
        --init)  INIT=true ;;
        *)       fail "Unknown argument: $arg" ;;
    esac
done

check_prerequisites
if $INIT; then init_data; fi
if $BUILD; then build_image; fi
deploy_to_vm
