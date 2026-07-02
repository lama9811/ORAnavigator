#!/bin/bash
set -euo pipefail

# =============================================================================
# ORANavigator Cloud Run Deployment Script
# =============================================================================
# Deploys backend, frontend, and ADK agent to Google Cloud Run
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Cloud Build API enabled (script enables it via `setup`)
#
# Usage:
#   ./deploy-cloudrun.sh [all|backend|frontend|adk]
#
# =============================================================================

# Configuration
PROJECT_ID="infra-vertex-494621-v1"
REGION="us-central1"
REPO_NAME="oranavigator"

# Service names
BACKEND_SERVICE="oranavigator-backend"
FRONTEND_SERVICE="oranavigator-frontend"
ADK_SERVICE="oranavigator-adk"

# Artifact Registry paths
AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/${REPO_NAME}"

# Image names
BACKEND_IMAGE="${AR_REPO}/backend:latest"
FRONTEND_IMAGE="${AR_REPO}/frontend:latest"
ADK_IMAGE="${AR_REPO}/adk-agent:latest"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[$(date +'%H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; exit 1; }

# =============================================================================
# Prerequisites Check
# =============================================================================
check_prerequisites() {
    log "Checking prerequisites..."

    command -v gcloud >/dev/null 2>&1 || error "gcloud CLI not installed"

    # Check gcloud auth
    if ! gcloud auth print-access-token >/dev/null 2>&1; then
        error "Not authenticated with gcloud. Run: gcloud auth login"
    fi

    # Set project
    gcloud config set project ${PROJECT_ID} --quiet

    log "Prerequisites OK"
}

# =============================================================================
# Enable required GCP APIs (idempotent)
# =============================================================================
enable_apis() {
    log "Enabling required GCP APIs..."
    gcloud services enable \
        run.googleapis.com \
        cloudbuild.googleapis.com \
        artifactregistry.googleapis.com \
        secretmanager.googleapis.com \
        aiplatform.googleapis.com \
        discoveryengine.googleapis.com \
        --project ${PROJECT_ID}
    log "APIs enabled"
}

# =============================================================================
# Create Artifact Registry (if not exists)
# =============================================================================
setup_artifact_registry() {
    log "Setting up Artifact Registry..."

    if ! gcloud artifacts repositories describe ${REPO_NAME} \
        --location=${REGION} >/dev/null 2>&1; then
        log "Creating Artifact Registry repository..."
        gcloud artifacts repositories create ${REPO_NAME} \
            --repository-format=docker \
            --location=${REGION} \
            --description="ORA Navigator container images"
    else
        log "Artifact Registry repository already exists"
    fi
}

# =============================================================================
# Build and Push Images
# =============================================================================

# Stage a clean copy of a source dir OUTSIDE the git working tree, then build
# from there. WORKAROUND: `gcloud builds submit` reliably crashes
# ("ERROR: gcloud crashed (OSError): unexpected end of data" while "Creating
# temporary archive") when the source dir is INSIDE this git repo -- observed
# on both backend/ (1242 files) and the tiny frontend/ context (81 files), so
# it is NOT a corrupted file. Building from a /tmp copy with no .git (and no
# venv/node_modules/dist) avoids it. (2026-05-29)
stage_clean() {
    local src="$1" dest="$2"
    rm -rf "$dest"
    mkdir -p "$dest"
    rsync -a \
        --exclude='.venv' --exclude='venv' --exclude='node_modules' \
        --exclude='dist' --exclude='build' --exclude='.git' \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='.env' --exclude='.env.*' --exclude='.pytest_cache' \
        "${src}/" "${dest}/"
}

build_backend() {
    log "Building backend image via Cloud Build..."
    local stage="/tmp/oranav-stage-backend"
    stage_clean "${SCRIPT_DIR}/backend" "$stage"
    gcloud builds submit "$stage" \
        --tag ${BACKEND_IMAGE} \
        --project ${PROJECT_ID}
}

build_frontend() {
    log "Building frontend image via Cloud Build..."

    # Get backend URL for API calls
    BACKEND_URL=$(gcloud run services describe ${BACKEND_SERVICE} \
        --region=${REGION} \
        --format='value(status.url)' 2>/dev/null || echo "")

    if [ -z "$BACKEND_URL" ]; then
        warn "Backend not deployed yet. Frontend will use relative URLs."
        BACKEND_URL=""
    fi

    local stage="/tmp/oranav-stage-frontend"
    stage_clean "${SCRIPT_DIR}/frontend" "$stage"

    # Generate the cloudbuild.yaml INSIDE the staged dir so it can pass
    # build-args to Cloud Build (and so '.' = the clean staged context).
    cat > "${stage}/cloudbuild.yaml" <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '--build-arg'
      - 'VITE_API_BASE_URL=${BACKEND_URL}'
      - '-t'
      - '${FRONTEND_IMAGE}'
      - '.'
images:
  - '${FRONTEND_IMAGE}'
EOF

    gcloud builds submit "$stage" \
        --config "${stage}/cloudbuild.yaml" \
        --project ${PROJECT_ID}
}

build_adk() {
    log "Building ADK agent image via Cloud Build..."
    local stage="/tmp/oranav-stage-adk"
    stage_clean "${SCRIPT_DIR}/adk_agent" "$stage"
    gcloud builds submit "$stage" \
        --tag ${ADK_IMAGE} \
        --project ${PROJECT_ID}
}

# =============================================================================
# Deploy Services
# =============================================================================
deploy_adk() {
    log "Deploying ADK agent service..."

    gcloud run deploy ${ADK_SERVICE} \
        --image ${ADK_IMAGE} \
        --region ${REGION} \
        --platform managed \
        --port 8080 \
        --memory 2Gi \
        --cpu 2 \
        --min-instances 1 \
        --cpu-boost \
        --max-instances 10 \
        --timeout 300 \
        --concurrency 80 \
        --no-allow-unauthenticated \
        --service-account "oranavigator-backend@${PROJECT_ID}.iam.gserviceaccount.com" \
        --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=TRUE,AGENT_MODEL=gemini-2.5-flash,ADK_APP_NAME=ora_navigator_unified,UNIFIED_DATASTORE_ID=projects/${PROJECT_ID}/locations/us/collections/default_collection/dataStores/oranavigator-kb-v8,VERTEX_AI_DATASTORE_ID=projects/${PROJECT_ID}/locations/us/collections/default_collection/dataStores/oranavigator-kb-v8,KB_PREFETCH_DATASTORE_ID=oranavigator-kb-v8"

    ADK_URL=$(gcloud run services describe ${ADK_SERVICE} \
        --region=${REGION} \
        --format='value(status.url)')

    log "ADK deployed at: ${ADK_URL}"
    echo "${ADK_URL}" > "${SCRIPT_DIR}/.adk-url"
}

deploy_backend() {
    log "Deploying backend service..."

    # Get ADK service URL
    ADK_URL=$(gcloud run services describe ${ADK_SERVICE} \
        --region=${REGION} \
        --format='value(status.url)' 2>/dev/null || echo "")

    if [ -z "$ADK_URL" ]; then
        error "ADK service not deployed. Run: ./deploy-cloudrun.sh adk"
    fi

    # Load env vars from .env file
    if [ -f "${SCRIPT_DIR}/.env" ]; then
        log "Loading environment variables from .env..."

        # Read specific vars we need
        DATABASE_URL=$(grep "^DATABASE_URL=" "${SCRIPT_DIR}/.env" | cut -d'=' -f2-)
        JWT_SECRET=$(grep "^JWT_SECRET=" "${SCRIPT_DIR}/.env" | cut -d'=' -f2-)
        OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" "${SCRIPT_DIR}/.env" | cut -d'=' -f2-)
        ADMIN_EMAIL=$(grep "^ADMIN_EMAIL=" "${SCRIPT_DIR}/.env" | cut -d'=' -f2-)
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" "${SCRIPT_DIR}/.env" | cut -d'=' -f2-)
    else
        error ".env file not found"
    fi

    gcloud run deploy ${BACKEND_SERVICE} \
        --image ${BACKEND_IMAGE} \
        --region ${REGION} \
        --platform managed \
        --port 5000 \
        --memory 1Gi \
        --cpu 1 \
        --min-instances 1 \
        --cpu-boost \
        --max-instances 20 \
        --timeout 300 \
        --concurrency 100 \
        --allow-unauthenticated \
        --add-cloudsql-instances "${PROJECT_ID}:${REGION}:oranavigator-db" \
        --service-account "oranavigator-backend@${PROJECT_ID}.iam.gserviceaccount.com" \
        --set-env-vars "^|^\
USE_VERTEX_AGENT=true|\
ADK_BASE_URL=${ADK_URL}|\
ADK_APP_NAME=ora_navigator_unified|\
GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|\
GOOGLE_CLOUD_LOCATION=${REGION}|\
GOOGLE_GENAI_USE_VERTEXAI=TRUE|\
AGENT_MODEL=gemini-2.5-flash|\
UNIFIED_DATASTORE_ID=projects/${PROJECT_ID}/locations/us/collections/default_collection/dataStores/oranavigator-kb-v8|\
VERTEX_AI_DATASTORE_ID=projects/${PROJECT_ID}/locations/us/collections/default_collection/dataStores/oranavigator-kb-v8|\
ACCESS_TOKEN_EXPIRE_MINUTES=240|\
ALGORITHM=HS256|\
SMTP_HOST=smtp.gmail.com|\
SMTP_PORT=587|\
SMTP_USER=noreplyinavigator@gmail.com|\
FROM_EMAIL=noreplyinavigator@gmail.com|\
APP_URL=https://ora.inavigator.ai|\
API_URL=https://oranavigator-backend-ollhkgeova-uc.a.run.app|\
CORS_ORIGINS=https://ora.inavigator.ai,https://oranavigator-frontend-ollhkgeova-uc.a.run.app,http://localhost:3001,http://127.0.0.1:3001" \
        --set-secrets "\
DATABASE_URL=ora-database-url:latest,\
JWT_SECRET=ora-jwt-secret:latest,\
ADMIN_EMAIL=ora-admin-email:latest,\
ADMIN_PASSWORD=ora-admin-password:latest,\
REDIS_URL=ora-redis-url:latest,\
SMTP_PASS=ora-smtp:latest"

    BACKEND_URL=$(gcloud run services describe ${BACKEND_SERVICE} \
        --region=${REGION} \
        --format='value(status.url)')

    log "Backend deployed at: ${BACKEND_URL}"
    echo "${BACKEND_URL}" > "${SCRIPT_DIR}/.backend-url"
}

deploy_frontend() {
    log "Deploying frontend service..."

    gcloud run deploy ${FRONTEND_SERVICE} \
        --image ${FRONTEND_IMAGE} \
        --region ${REGION} \
        --platform managed \
        --port 8080 \
        --memory 512Mi \
        --cpu 1 \
        --min-instances 0 \
        --max-instances 10 \
        --timeout 60 \
        --concurrency 200 \
        --allow-unauthenticated

    FRONTEND_URL=$(gcloud run services describe ${FRONTEND_SERVICE} \
        --region=${REGION} \
        --format='value(status.url)')

    log "Frontend deployed at: ${FRONTEND_URL}"
    echo "${FRONTEND_URL}" > "${SCRIPT_DIR}/.frontend-url"
}

# =============================================================================
# Setup Secrets (one-time)
# =============================================================================
setup_secrets() {
    log "Setting up Secret Manager secrets..."

    if [ ! -f "${SCRIPT_DIR}/.env" ]; then
        error ".env file not found"
    fi

    # ORA secrets are pre-created (ora-database-url, ora-jwt-secret, ora-admin-email, ora-admin-password)
    # ORA Navigator uses generic-named secrets in the same project; do NOT overwrite them here.
    # Map .env vars → ora-prefixed secrets (idempotent: creates if missing, adds a new version if present)
    declare -a ENV_TO_SECRET=(
        "DATABASE_URL:ora-database-url"
        "JWT_SECRET:ora-jwt-secret"
        "ADMIN_EMAIL:ora-admin-email"
        "ADMIN_PASSWORD:ora-admin-password"
    )

    for MAPPING in "${ENV_TO_SECRET[@]}"; do
        ENV_NAME="${MAPPING%%:*}"
        SECRET_NAME="${MAPPING##*:}"
        SECRET_VALUE=$(grep "^${ENV_NAME}=" "${SCRIPT_DIR}/.env" | cut -d'=' -f2-)

        if [ -z "$SECRET_VALUE" ]; then
            warn "Value for ${ENV_NAME} not found in .env, skipping ${SECRET_NAME}..."
            continue
        fi

        if gcloud secrets describe ${SECRET_NAME} >/dev/null 2>&1; then
            log "Adding new version to: ${SECRET_NAME}"
            echo -n "${SECRET_VALUE}" | gcloud secrets versions add ${SECRET_NAME} --data-file=-
        else
            log "Creating secret: ${SECRET_NAME}"
            echo -n "${SECRET_VALUE}" | gcloud secrets create ${SECRET_NAME} --data-file=-
        fi

        gcloud secrets add-iam-policy-binding ${SECRET_NAME} \
            --member="serviceAccount:oranavigator-backend@${PROJECT_ID}.iam.gserviceaccount.com" \
            --role="roles/secretmanager.secretAccessor" \
            --quiet 2>&1 | tail -1
    done

    log "Secrets configured"
}

# =============================================================================
# IAM Setup (one-time)
# =============================================================================
setup_iam() {
    log "Setting up IAM permissions..."

    # Create service account if not exists
    if ! gcloud iam service-accounts describe \
        "oranavigator-backend@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
        log "Creating service account..."
        gcloud iam service-accounts create oranavigator-backend \
            --display-name="ORANavigator Backend"
    fi

    SA_EMAIL="oranavigator-backend@${PROJECT_ID}.iam.gserviceaccount.com"

    # Grant necessary roles
    ROLES=(
        "roles/aiplatform.user"
        "roles/discoveryengine.viewer"
        "roles/storage.objectViewer"
        "roles/secretmanager.secretAccessor"
        "roles/run.invoker"
        "roles/cloudsql.client"
    )

    for ROLE in "${ROLES[@]}"; do
        log "Granting ${ROLE}..."
        gcloud projects add-iam-policy-binding ${PROJECT_ID} \
            --member="serviceAccount:${SA_EMAIL}" \
            --role="${ROLE}" \
            --quiet
    done

    # Allow backend to invoke ADK service
    gcloud run services add-iam-policy-binding ${ADK_SERVICE} \
        --region=${REGION} \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/run.invoker" \
        --quiet 2>/dev/null || true

    log "IAM configured"
}

# =============================================================================
# Quick Deploy (rebuild and redeploy changed services)
# =============================================================================
quick_deploy() {
    local service=$1

    case $service in
        backend)
            build_backend
            deploy_backend
            ;;
        frontend)
            build_frontend
            deploy_frontend
            ;;
        adk)
            build_adk
            deploy_adk
            ;;
        *)
            error "Unknown service: $service"
            ;;
    esac
}

# =============================================================================
# Full Deploy (all services)
# =============================================================================
deploy_all() {
    log "========================================="
    log "Starting full deployment to Cloud Run..."
    log "========================================="

    check_prerequisites
    enable_apis
    setup_artifact_registry

    # Build all images
    build_adk
    build_backend

    # Deploy in order (ADK first, then backend, then frontend)
    deploy_adk
    setup_iam
    deploy_backend

    # Build frontend with backend URL
    build_frontend
    deploy_frontend

    log "========================================="
    log "Deployment complete!"
    log "========================================="
    log "Frontend: $(cat ${SCRIPT_DIR}/.frontend-url 2>/dev/null || echo 'N/A')"
    log "Backend:  $(cat ${SCRIPT_DIR}/.backend-url 2>/dev/null || echo 'N/A')"
    log "ADK:      $(cat ${SCRIPT_DIR}/.adk-url 2>/dev/null || echo 'N/A')"
    log "========================================="
}

# =============================================================================
# Status Check
# =============================================================================
status() {
    log "Cloud Run Services Status:"
    echo ""

    for SERVICE in ${ADK_SERVICE} ${BACKEND_SERVICE} ${FRONTEND_SERVICE}; do
        URL=$(gcloud run services describe ${SERVICE} \
            --region=${REGION} \
            --format='value(status.url)' 2>/dev/null || echo "NOT DEPLOYED")
        echo "  ${SERVICE}: ${URL}"
    done
    echo ""
}

# =============================================================================
# Main
# =============================================================================
main() {
    local command=${1:-all}

    case $command in
        all)
            deploy_all
            ;;
        backend)
            check_prerequisites
            quick_deploy backend
            ;;
        frontend)
            check_prerequisites
            quick_deploy frontend
            ;;
        adk)
            check_prerequisites
            setup_artifact_registry
            quick_deploy adk
            ;;
        setup)
            check_prerequisites
            enable_apis
            setup_artifact_registry
            setup_iam
            setup_secrets
            ;;
        secrets)
            check_prerequisites
            setup_secrets
            ;;
        status)
            status
            ;;
        *)
            echo "Usage: $0 [all|backend|frontend|adk|setup|secrets|status]"
            echo ""
            echo "Commands:"
            echo "  all       - Deploy all services (default)"
            echo "  backend   - Build and deploy backend only"
            echo "  frontend  - Build and deploy frontend only"
            echo "  adk       - Build and deploy ADK agent only"
            echo "  setup     - One-time setup (Artifact Registry, Secrets, IAM)"
            echo "  secrets   - Update secrets from .env file"
            echo "  status    - Show deployment status"
            exit 1
            ;;
    esac
}

main "$@"
