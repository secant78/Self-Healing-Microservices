#!/usr/bin/env bash
# deploy.sh — Deploy the Self-Healing Microservices stack to Azure Container Apps.
#
# Usage:
#   ./scripts/deploy.sh [--env <dev|staging|prod>] [--location <azure-region>]
#                       [--subscription <subscription-id>] [--skip-images]
#
# Prerequisites: az CLI logged in, docker daemon running, jq installed.

set -euo pipefail

# ---------------------------------------------------------------------------
# Default configuration — override via flags or environment variables
# ---------------------------------------------------------------------------
ENVIRONMENT="${DEPLOY_ENV:-dev}"
LOCATION="${DEPLOY_LOCATION:-eastus}"
SUBSCRIPTION="${AZURE_SUBSCRIPTION_ID:-}"
SKIP_IMAGES="${SKIP_IMAGES:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BICEP_MAIN="${INFRA_DIR}/bicep/main.bicep"
PARAMS_FILE="${INFRA_DIR}/bicep/parameters/${ENVIRONMENT}.parameters.json"

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)         ENVIRONMENT="$2";    shift 2 ;;
    --location)    LOCATION="$2";       shift 2 ;;
    --subscription) SUBSCRIPTION="$2"; shift 2 ;;
    --skip-images) SKIP_IMAGES="true";  shift   ;;
    *) error "Unknown argument: $1" ;;
  esac
done

RESOURCE_GROUP="rg-selfhealing-${ENVIRONMENT}"
DEPLOYMENT_NAME="selfhealing-${ENVIRONMENT}-$(date +%Y%m%d%H%M%S)"

# ---------------------------------------------------------------------------
# Validate prerequisites
# ---------------------------------------------------------------------------
info "Validating prerequisites..."

if ! command -v az &>/dev/null; then
  error "Azure CLI (az) is not installed. Run scripts/setup.sh first."
fi

if ! command -v docker &>/dev/null; then
  warn "Docker is not installed or not in PATH. Image build/push steps will be skipped."
  SKIP_IMAGES="true"
fi

if ! command -v jq &>/dev/null; then
  warn "jq is not installed. Output parsing will use az CLI --query instead."
fi

# ---------------------------------------------------------------------------
# Azure authentication check
# ---------------------------------------------------------------------------
info "Checking Azure login status..."
if ! az account show &>/dev/null; then
  info "Not logged in — starting interactive login..."
  az login
fi

if [[ -n "${SUBSCRIPTION}" ]]; then
  info "Setting active subscription to: ${SUBSCRIPTION}"
  az account set --subscription "${SUBSCRIPTION}"
fi

CURRENT_SUB=$(az account show --query "name" -o tsv)
info "Active subscription: ${CURRENT_SUB}"

# ---------------------------------------------------------------------------
# Validate parameters file
# ---------------------------------------------------------------------------
if [[ ! -f "${PARAMS_FILE}" ]]; then
  error "Parameters file not found: ${PARAMS_FILE}"
fi
info "Using parameters file: ${PARAMS_FILE}"

# ---------------------------------------------------------------------------
# Create resource group (idempotent)
# ---------------------------------------------------------------------------
info "Ensuring resource group '${RESOURCE_GROUP}' exists in '${LOCATION}'..."
az group create \
  --name "${RESOURCE_GROUP}" \
  --location "${LOCATION}" \
  --output none
success "Resource group ready."

# ---------------------------------------------------------------------------
# Resolve ACR name from parameters file
# ---------------------------------------------------------------------------
ACR_NAME=$(jq -r '.parameters.acrName.value // ""' "${PARAMS_FILE}")
if [[ -z "${ACR_NAME}" ]]; then
  ACR_NAME="acrselfhealing${ENVIRONMENT}"
  info "acrName not set in parameters — will use auto-generated name: ${ACR_NAME}"
fi

# ---------------------------------------------------------------------------
# Build and push Docker images (unless --skip-images)
# ---------------------------------------------------------------------------
if [[ "${SKIP_IMAGES}" != "true" ]]; then
  info "Logging Docker into ACR: ${ACR_NAME}.azurecr.io"
  az acr login --name "${ACR_NAME}" 2>/dev/null || warn "ACR login failed — registry may not exist yet; images will be pushed after first deployment."

  build_and_push() {
    local SERVICE="$1"
    local CONTEXT="$2"
    local TAG="${ACR_NAME}.azurecr.io/${SERVICE}:latest"

    if [[ -d "${CONTEXT}" ]]; then
      info "Building image for ${SERVICE}..."
      docker build -t "${TAG}" "${CONTEXT}"
      info "Pushing ${TAG}..."
      docker push "${TAG}"
      success "Pushed ${TAG}"
    else
      warn "Context directory not found for ${SERVICE}: ${CONTEXT} — skipping build."
    fi
  }

  REPO_ROOT="$(cd "${INFRA_DIR}/.." && pwd)"
  build_and_push "frontend"        "${REPO_ROOT}/services/frontend"
  build_and_push "order-service"   "${REPO_ROOT}/services/order-service"
  build_and_push "payment-service" "${REPO_ROOT}/services/payment-service"
fi

# ---------------------------------------------------------------------------
# Deploy Bicep template
# ---------------------------------------------------------------------------
info "Starting Bicep deployment '${DEPLOYMENT_NAME}'..."
info "  Resource group : ${RESOURCE_GROUP}"
info "  Template       : ${BICEP_MAIN}"
info "  Parameters     : ${PARAMS_FILE}"

DEPLOY_OUTPUT=$(az deployment group create \
  --name "${DEPLOYMENT_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --template-file "${BICEP_MAIN}" \
  --parameters "@${PARAMS_FILE}" \
  --output json)

success "Deployment completed."

# ---------------------------------------------------------------------------
# Extract and display outputs
# ---------------------------------------------------------------------------
extract_output() {
  echo "${DEPLOY_OUTPUT}" | jq -r ".properties.outputs.${1}.value // \"N/A\""
}

FRONTEND_URL=$(extract_output "frontendUrl")
ORDER_URL=$(extract_output "orderServiceUrl")
PAYMENT_URL=$(extract_output "paymentServiceUrl")
APPINSIGHTS_KEY=$(extract_output "applicationInsightsKey")

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deployment successful!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "  Frontend URL          : ${CYAN}${FRONTEND_URL}${NC}"
echo -e "  Order Service URL     : ${CYAN}${ORDER_URL}${NC}"
echo -e "  Payment Service URL   : ${CYAN}${PAYMENT_URL}${NC}"
echo -e "  App Insights Key      : ${YELLOW}${APPINSIGHTS_KEY}${NC}"
echo ""

# ---------------------------------------------------------------------------
# Smoke test — verify frontend returns HTTP 200
# ---------------------------------------------------------------------------
if command -v curl &>/dev/null && [[ "${FRONTEND_URL}" != "N/A" ]]; then
  info "Running smoke test against ${FRONTEND_URL} ..."
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "${FRONTEND_URL}" || echo "000")
  if [[ "${HTTP_STATUS}" == "200" ]]; then
    success "Smoke test passed (HTTP ${HTTP_STATUS})."
  else
    warn "Smoke test returned HTTP ${HTTP_STATUS}. The app may still be starting up."
  fi
fi
