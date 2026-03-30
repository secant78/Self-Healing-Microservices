#!/usr/bin/env bash
# setup.sh — One-time environment bootstrap for the Self-Healing Microservices project.
#
# Run this once before the first deploy to:
#   1. Verify required tools are installed.
#   2. Add the Container Apps CLI extension.
#   3. Register required Azure resource providers.
#   4. Create a service principal for GitHub Actions OIDC authentication.
#
# Usage:
#   ./scripts/setup.sh [--subscription <id>] [--gh-repo <owner/repo>]

set -euo pipefail

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------
SUBSCRIPTION="${AZURE_SUBSCRIPTION_ID:-}"
GITHUB_REPO="${GITHUB_REPO:-}"          # e.g. myorg/self-healing-microservices
SP_NAME="sp-selfhealing-github-actions"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
    --subscription) SUBSCRIPTION="$2"; shift 2 ;;
    --gh-repo)      GITHUB_REPO="$2";  shift 2 ;;
    *) error "Unknown argument: $1" ;;
  esac
done

# ---------------------------------------------------------------------------
# Step 1 — Check required tools
# ---------------------------------------------------------------------------
info "========================================="
info "  Step 1: Checking required tools"
info "========================================="

MISSING_TOOLS=()

check_tool() {
  local TOOL="$1"
  local INSTALL_HINT="$2"
  if command -v "${TOOL}" &>/dev/null; then
    VERSION=$(${TOOL} --version 2>&1 | head -1 || echo "unknown version")
    success "${TOOL} is installed: ${VERSION}"
  else
    warn "${TOOL} is NOT installed. ${INSTALL_HINT}"
    MISSING_TOOLS+=("${TOOL}")
  fi
}

check_tool "az"     "Install from https://docs.microsoft.com/cli/azure/install-azure-cli"
check_tool "docker" "Install from https://docs.docker.com/get-docker/"
check_tool "git"    "Install from https://git-scm.com/downloads"
check_tool "jq"     "Install via your package manager (brew install jq / apt install jq)"

if [[ ${#MISSING_TOOLS[@]} -gt 0 ]]; then
  error "Missing required tools: ${MISSING_TOOLS[*]}. Install them and re-run this script."
fi

# ---------------------------------------------------------------------------
# Step 2 — Azure login
# ---------------------------------------------------------------------------
info "========================================="
info "  Step 2: Azure authentication"
info "========================================="

if ! az account show &>/dev/null; then
  info "Not logged in — starting interactive login..."
  az login
fi

if [[ -n "${SUBSCRIPTION}" ]]; then
  info "Setting active subscription to: ${SUBSCRIPTION}"
  az account set --subscription "${SUBSCRIPTION}"
fi

SUBSCRIPTION_ID=$(az account show --query "id" -o tsv)
SUBSCRIPTION_NAME=$(az account show --query "name" -o tsv)
success "Active subscription: ${SUBSCRIPTION_NAME} (${SUBSCRIPTION_ID})"

# ---------------------------------------------------------------------------
# Step 3 — Add Azure Container Apps CLI extension
# ---------------------------------------------------------------------------
info "========================================="
info "  Step 3: Azure CLI extensions"
info "========================================="

info "Adding / updating the containerapp extension..."
az extension add --name containerapp --upgrade --allow-preview false
success "containerapp extension is ready."

# ---------------------------------------------------------------------------
# Step 4 — Register required resource providers
# ---------------------------------------------------------------------------
info "========================================="
info "  Step 4: Registering resource providers"
info "========================================="

PROVIDERS=(
  "Microsoft.App"
  "Microsoft.ContainerRegistry"
  "Microsoft.OperationalInsights"
  "Microsoft.Insights"
  "Microsoft.AlertsManagement"
)

for PROVIDER in "${PROVIDERS[@]}"; do
  STATE=$(az provider show --namespace "${PROVIDER}" --query "registrationState" -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "${STATE}" == "Registered" ]]; then
    success "${PROVIDER} is already registered."
  else
    info "Registering ${PROVIDER} (this may take a minute)..."
    az provider register --namespace "${PROVIDER}" --wait
    success "${PROVIDER} registered."
  fi
done

# ---------------------------------------------------------------------------
# Step 5 — Create service principal for GitHub Actions (OIDC federated)
# ---------------------------------------------------------------------------
info "========================================="
info "  Step 5: GitHub Actions service principal"
info "========================================="

if [[ -z "${GITHUB_REPO}" ]]; then
  warn "No --gh-repo provided. Skipping service principal creation."
  warn "Re-run with: --gh-repo <owner/repo>  to create the SP automatically."
else
  GITHUB_ORG="${GITHUB_REPO%%/*}"
  REPO_NAME="${GITHUB_REPO##*/}"

  info "Creating/verifying service principal: ${SP_NAME}"

  # Create the SP (or retrieve existing) with Contributor on the subscription.
  SP_JSON=$(az ad sp create-for-rbac \
    --name "${SP_NAME}" \
    --role "Contributor" \
    --scopes "/subscriptions/${SUBSCRIPTION_ID}" \
    --sdk-auth \
    2>/dev/null || true)

  if [[ -z "${SP_JSON}" ]]; then
    warn "Service principal '${SP_NAME}' already exists. Fetching client ID..."
    SP_CLIENT_ID=$(az ad sp list --display-name "${SP_NAME}" --query "[0].appId" -o tsv)
    info "Existing SP client ID: ${SP_CLIENT_ID}"
  else
    SP_CLIENT_ID=$(echo "${SP_JSON}" | jq -r '.clientId')
    SP_TENANT_ID=$(echo "${SP_JSON}" | jq -r '.tenantId')
    info "Created SP with client ID: ${SP_CLIENT_ID}"
  fi

  # Add federated credential for OIDC (branch: main)
  info "Adding OIDC federated credential for ${GITHUB_REPO} (branch: main)..."
  az ad app federated-credential create \
    --id "${SP_CLIENT_ID}" \
    --parameters "{
      \"name\": \"${REPO_NAME}-main\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"repo:${GITHUB_REPO}:ref:refs/heads/main\",
      \"description\": \"GitHub Actions OIDC for main branch\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }" 2>/dev/null || warn "Federated credential may already exist — continuing."

  # Add federated credential for pull_request events
  info "Adding OIDC federated credential for ${GITHUB_REPO} (pull_request)..."
  az ad app federated-credential create \
    --id "${SP_CLIENT_ID}" \
    --parameters "{
      \"name\": \"${REPO_NAME}-pr\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"repo:${GITHUB_REPO}:pull_request\",
      \"description\": \"GitHub Actions OIDC for pull requests\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }" 2>/dev/null || warn "Federated PR credential may already exist — continuing."

  TENANT_ID="${SP_TENANT_ID:-$(az account show --query "tenantId" -o tsv)}"

  echo ""
  echo -e "${GREEN}========================================${NC}"
  echo -e "${GREEN}  GitHub Actions Secrets${NC}"
  echo -e "${GREEN}========================================${NC}"
  echo "Add these secrets to your GitHub repository (Settings > Secrets > Actions):"
  echo ""
  echo -e "  ${YELLOW}AZURE_CLIENT_ID${NC}       : ${SP_CLIENT_ID}"
  echo -e "  ${YELLOW}AZURE_TENANT_ID${NC}       : ${TENANT_ID}"
  echo -e "  ${YELLOW}AZURE_SUBSCRIPTION_ID${NC} : ${SUBSCRIPTION_ID}"
  echo ""
  echo "No client secret is required — authentication uses OIDC federated identity."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Next step: run ./scripts/deploy.sh --env dev"
