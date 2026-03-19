#!/bin/bash
# 部署 pipeline 服务到 GCP COS VM
# Usage: bash deploy/deploy-pipeline.sh [--build]
#   --build  构建新镜像（不传则复用已有镜像）
#   无参数    仅部署容器（拉取已有镜像 + 上传 .env + 重启）
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────
PROJECT="pikppo"
REGION="asia-east1"
ZONE="asia-southeast1-a"
REPO="dubora"
IMAGE="dubora-pipeline"
IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest"

WEB_VM_NAME="dubora-web-sg"
VM_NAME="dubora-pipeline-sg"
VM_USER="nianien"
CONTAINER_NAME="dubora-pipeline"
DATA_DIR="/var/dubora/data"

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
    log "Building pipeline image via Cloud Build..."
    cd "$PROJECT_DIR"
    gcloud builds submit --config=deploy/cloudbuild-pipeline.yaml --substitutions=_IMAGE_URL="$IMAGE_URL" .
}

# ── 部署容器 ─────────────────────────────────────────────
resolve_web_ip() {
    log "Resolving web VM internal IP..."
    WEB_IP=$(gcloud compute instances describe "$WEB_VM_NAME" \
        --zone="$ZONE" --format="get(networkInterfaces[0].networkIP)")
    [ -n "$WEB_IP" ] || fail "Cannot resolve web VM IP"
    API_URL="http://${WEB_IP}:8765"
    log "Web API: ${API_URL}"
}

deploy_to_vm() {
    log "Uploading .env..."
    vm_scp "$PROJECT_DIR/.env" "~/.env.dubora"

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
            -v ${DATA_DIR}:/data \
            --env-file ~/.env.dubora \
            -e API_URL=${API_URL} \
            -e GOOGLE_APPLICATION_CREDENTIALS=/data/.gcp/pikppo-dubora.json \
            ${IMAGE_URL}
        docker ps --filter name=${CONTAINER_NAME}
    "
    log "Pipeline deployed (API_URL=${API_URL})."
}

# ── 用法 ────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: bash deploy/deploy-pipeline.sh [OPTIONS]

Deploy dubora-pipeline (pipeline worker) to GCP VM (${VM_NAME})
Connects to web API at ${WEB_VM_NAME} via internal IP.

Options:
  --build   Build new Docker image via Cloud Build
  --help    Show this help

Without options: pull existing image + upload .env + restart container.

Examples:
  bash deploy/deploy-pipeline.sh               # Deploy only
  bash deploy/deploy-pipeline.sh --build       # Build + deploy
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
resolve_web_ip
deploy_to_vm
