#!/bin/bash
# 部署 web 服务到 GCP COS VM
# Usage: bash deploy/deploy-web.sh [--build]
#   --build  构建新镜像（不传则复用已有镜像）
#   无参数    仅部署容器（拉取已有镜像 + 上传 .env + 重启）
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────
PROJECT="pikppo"
REGION="asia-east1"
ZONE="asia-southeast1-a"
REPO="dubora"
IMAGE="dubora-web"
IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest"

VM_NAME="dubora-web-sg"
VM_USER="nianien"
CONTAINER_NAME="dubora-web"
DATA_DIR="/var/dubora/data"
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
        sudo mkdir -p ${DATA_DIR}
        sudo chown -R \$(id -u):\$(id -g) ${DATA_DIR%/*}
        mkdir -p ${DATA_DIR}/.gcp
    "

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

Deploy dubora-web to GCP VM (${VM_NAME})
DB is PostgreSQL (Cloud SQL), configured via DB_URL in .env.

Options:
  --build   Build new Docker image via Cloud Build
  --help    Show this help

Without options: pull existing image + upload .env + restart container.

Examples:
  bash deploy/deploy-web.sh                # Deploy only
  bash deploy/deploy-web.sh --build        # Build + deploy
EOF
    exit 0
}

# ── 主流程 ────────────────────────────────────────────────
BUILD=false
for arg in "$@"; do
    case "$arg" in
        --build) BUILD=true ;;
        --help|-h) usage ;;
        *)       fail "Unknown argument: $arg" ;;
    esac
done

check_prerequisites
if $BUILD; then build_image; fi
deploy_to_vm
