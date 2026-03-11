#!/bin/bash
# 部署 task 服务到 GCP COS VM
# Usage: bash deploy/deploy-task.sh [--build]
#   --build  构建新镜像（不传则复用已有镜像）
#   无参数    仅部署容器（拉取已有镜像 + 上传 .env + 重启）
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────
PROJECT="pikppo"
REGION="asia-east1"
ZONE="asia-southeast1-a"
REPO="dubora"
IMAGE="dubora-task"
IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest"

VM_NAME="sg-dubora-task"
VM_USER="nianien_gmail_com"
CONTAINER_NAME="dubora-task"
DATA_DIR="/mnt/disks/data"

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

# ── 构建镜像 ─────────────────────────────────────────────
build_image() {
    log "Building task image via Cloud Build..."
    cd "$PROJECT_DIR"
    gcloud builds submit --config=deploy/cloudbuild-task.yaml --substitutions=_IMAGE_URL="$IMAGE_URL" .
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
            -v ${DATA_DIR}:/data \
            --env-file ~/.env.dubora \
            ${IMAGE_URL}
        docker ps --filter name=${CONTAINER_NAME}
    "
    log "Task deployed."
}

# ── 主流程 ────────────────────────────────────────────────
BUILD=false
for arg in "$@"; do
    case "$arg" in
        --build) BUILD=true ;;
        *)       fail "Unknown argument: $arg" ;;
    esac
done

check_prerequisites
if $BUILD; then build_image; fi
deploy_to_vm
