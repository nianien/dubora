#!/bin/bash
# 部署 pipeline 服务到 GCP COS VM
# Usage: bash deploy/deploy-pipeline.sh [--no-build]
#   无参数      构建新镜像 + 部署（默认）
#   --no-build  仅部署容器（复用已有镜像 + 上传 .env + 重启）
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────
PROJECT="pikppo"
REGION="asia-east1"
ZONE="asia-southeast1-a"
REPO="dubora"
IMAGE="dubora-pipeline"
IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE}:latest"

API_URL="https://dub.pikppo.com"   # web 域名（pipeline 通过它调 /api/worker/*）

VM_NAME="dubora-pipeline-sg"
VM_USER="${VM_USER:-$USER}"   # 默认用本地用户名（gcloud SSH 在 VM 上会建对应账户）
CONTAINER_NAME="dubora-pipeline"
DATA_DIR="/var/dubora/data"

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

# ── 构建镜像 ─────────────────────────────────────────────
build_image() {
    log "Building pipeline image via Cloud Build..."
    cd "$PROJECT_DIR"
    gcloud builds submit --config=deploy/cloudbuild-pipeline.yaml --substitutions=_IMAGE_URL="$IMAGE_URL" .
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

Deploy dubora-pipeline (pipeline worker) to GCP VM (${VM_NAME}).
Connects to web API at ${API_URL}.

Options:
  --no-build  Skip image build; pull existing image + restart container
  --help      Show this help

Without options: build new image via Cloud Build, then deploy.

Examples:
  bash deploy/deploy-pipeline.sh               # Build + deploy (default)
  bash deploy/deploy-pipeline.sh --no-build    # Deploy only (reuse last image)
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
deploy_to_vm
