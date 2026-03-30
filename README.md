# Self-Healing Microservices Pipeline

A production-grade Azure microservices system that instruments distributed tracing with OpenTelemetry, monitors service health with KQL queries in Application Insights, and automatically remediates failures using an Azure Function self-healing loop.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Azure Container Apps                        │
│                                                                 │
│   ┌──────────────┐     ┌──────────────┐     ┌───────────────┐  │
│   │   Frontend   │────▶│ Order Service│────▶│Payment Service│  │
│   │   Node.js    │     │   .NET 8     │     │  Python/FastAPI│  │
│   │   Port 3000  │     │  Port 8080   │     │   Port 8000   │  │
│   └──────┬───────┘     └──────┬───────┘     └───────┬───────┘  │
│          │                    │                      │  ⚡ chaos │
│          └────────────────────┴──────────────────────┘          │
│                               │                                 │
│                    OpenTelemetry SDK (all services)             │
│                               │                                 │
│                    ┌──────────▼──────────┐                      │
│                    │  Application Insights│                      │
│                    │   + Azure Monitor    │                      │
│                    └──────────┬──────────┘                      │
│                               │ Alert fires                     │
│                    ┌──────────▼──────────┐                      │
│                    │  Self-Healing Azure  │                      │
│                    │      Function        │                      │
│                    │  (RestartContainer)  │                      │
│                    └──────────┬──────────┘                      │
│                               │ az containerapp restart         │
│                               ▼                                 │
│                     Affected Container App                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start (Local)](#quick-start-local)
3. [Architecture](#architecture)
4. [Phase 1: Instrumentation and Distributed Tracing](#phase-1-instrumentation-and-distributed-tracing)
5. [Phase 2: KQL Queries and Dashboards](#phase-2-kql-queries-and-dashboards)
6. [Phase 3: Self-Healing Loop](#phase-3-self-healing-loop)
7. [Environment Variables](#environment-variables)
8. [Chaos Testing Guide](#chaos-testing-guide)
9. [Application Map](#application-map)
10. [Deployment to Azure](#deployment-to-azure)
11. [CI/CD Workflows](#cicd-workflows)
12. [Contributing](#contributing)

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Azure CLI | 2.60+ | Bicep deployment, ACA management |
| Docker Desktop | 4.25+ | Local container builds and compose |
| .NET SDK | 8.0 | Order Service build and test |
| Node.js | 20 LTS | Frontend build and test |
| Python | 3.12 | Payment Service and Azure Function |
| Azure Functions Core Tools | 4.x | Local Function development |

Install the Azure Functions Core Tools:

```bash
npm install -g azure-functions-core-tools@4 --unsafe-perm true
```

---

## Quick Start (Local)

Clone the repository and start the full stack with Docker Compose. Jaeger handles distributed tracing locally instead of Application Insights.

```bash
git clone <your-repo-url>
cd Self-Healing-Microservices

# Start all services (first run builds images)
docker compose up --build

# Or start in detached mode
docker compose up --build -d
```

Once running, open the following URLs:

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Order Service | http://localhost:8080 |
| Payment Service | http://localhost:8000/docs |
| Jaeger UI | http://localhost:16686 |

To place a test order and generate traces:

```bash
curl -X POST http://localhost:3000/api/order \
  -H "Content-Type: application/json" \
  -d '{"item": "widget", "quantity": 2, "price": 19.99}'
```

Navigate to http://localhost:16686, select `frontend` from the Service dropdown, and click **Find Traces** to see the distributed trace spanning all three services.

---

## Architecture

### Services

**Frontend (Node.js)**
- Express.js HTTP server on port 3000.
- Accepts user orders and forwards them to the Order Service via REST.
- Instrumented with `@opentelemetry/sdk-node` and `@opentelemetry/auto-instrumentations-node`.
- Propagates W3C `traceparent` headers on all outbound requests, enabling end-to-end trace correlation.

**Order Service (.NET 8)**
- ASP.NET Core minimal API on port 8080.
- Validates orders and calls the Payment Service to charge the customer.
- Uses `OpenTelemetry.Extensions.Hosting`, `OpenTelemetry.Instrumentation.AspNetCore`, and `OpenTelemetry.Instrumentation.Http`.
- Exports spans to Application Insights (Azure) or Jaeger OTLP (local).

**Payment Service (Python / FastAPI)**
- FastAPI application on port 8000 with Uvicorn.
- Processes payment transactions. Exposes a chaos mode toggle endpoint (`POST /circuit-breaker/open`, `POST /circuit-breaker/close`) for self-healing demonstrations.
- Uses `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, and `opentelemetry-exporter-otlp`.

### OpenTelemetry Flow

```
Service Code
    │
    ├── opentelemetry-sdk (traces, context propagation)
    │
    ├── OTLP Exporter
    │       │
    │       ├── [Azure]  Azure Monitor Exporter -> Application Insights
    │       │
    │       └── [Local]  OTLP/HTTP -> Jaeger:4318
    │
Application Insights
    │
    ├── KQL Queries (Log Analytics Workspace)
    ├── Azure Monitor Alerts
    └── Application Map (auto-generated topology)
```

### Infrastructure

All Azure resources are provisioned with Bicep templates in `infrastructure/bicep/`. The main resources are:

- Azure Container Apps Environment with Log Analytics Workspace
- Azure Container Registry (ACR) for storing Docker images
- Application Insights connected to the Container Apps Environment
- Azure Function App for the self-healing automation
- User-Assigned Managed Identity with roles: `AcrPull`, `Contributor` (scoped to resource group)

---

## Phase 1: Instrumentation and Distributed Tracing

### Step 1: Deploy the Infrastructure

```bash
# Create the resource group
az group create --name rg-self-healing-dev --location eastus

# Deploy all resources
az deployment group create \
  --resource-group rg-self-healing-dev \
  --template-file infrastructure/bicep/main.bicep \
  --parameters @infrastructure/bicep/parameters/dev.parameters.json
```

### Step 2: Configure Application Insights Connection Strings

After deployment, retrieve the connection string and set it in your Container Apps:

```bash
# Get the connection string
CONNECTION_STRING=$(az monitor app-insights component show \
  --app appi-self-healing-dev \
  --resource-group rg-self-healing-dev \
  --query connectionString -o tsv)

# Set it on each Container App
for app in frontend order-service payment-service; do
  az containerapp update \
    --name $app \
    --resource-group rg-self-healing-dev \
    --set-env-vars APPLICATIONINSIGHTS_CONNECTION_STRING="$CONNECTION_STRING"
done
```

### Step 3: Verify Trace Propagation

Place an order through the frontend and verify the trace appears in Application Insights:

```bash
FRONTEND_URL=$(az containerapp show \
  --name frontend \
  --resource-group rg-self-healing-dev \
  --query properties.configuration.ingress.fqdn -o tsv)

curl -X POST https://$FRONTEND_URL/api/order \
  -H "Content-Type: application/json" \
  -d '{"item": "widget", "quantity": 1, "price": 9.99}'
```

In the Azure Portal, navigate to **Application Insights** > **Transaction search** and search for traces from the last 30 minutes. A single order should produce one root span from the frontend with child spans for the order-service and payment-service calls.

### Step 4: View the Application Map

Navigate to **Application Insights** > **Application Map**. After several requests, Azure generates a topology graph showing:

- Frontend calling Order Service
- Order Service calling Payment Service
- Failure rates and average latency on each edge
- Components sized by call volume

---

## Phase 2: KQL Queries and Dashboards

All KQL queries live in `monitoring/kql-queries/`. Run them in **Application Insights** > **Logs**.

### P95 Latency by Service

File: `monitoring/kql-queries/p95-latency.kql`

```kql
requests
| where timestamp > ago(1h)
| summarize
    P50 = percentile(duration, 50),
    P95 = percentile(duration, 95),
    P99 = percentile(duration, 99),
    RequestCount = count()
  by cloud_RoleName, bin(timestamp, 5m)
| order by timestamp desc
```

Expected output (values are illustrative):

| cloud_RoleName  | bin_timestamp       | P50   | P95    | P99    | RequestCount |
|-----------------|---------------------|-------|--------|--------|--------------|
| frontend        | 2024-01-15 10:05:00 | 42.1  | 187.3  | 412.0  | 240          |
| order-service   | 2024-01-15 10:05:00 | 38.4  | 145.2  | 298.7  | 238          |
| payment-service | 2024-01-15 10:05:00 | 51.9  | 203.8  | 891.2  | 238          |

### Error Rate Alert Query

File: `monitoring/kql-queries/error-rate.kql`

The primary query detects error rate spikes on the payment service and filters to buckets exceeding the 10% threshold. Create an Azure Monitor alert rule using this query with:

- **Aggregation granularity**: 5 minutes
- **Evaluation frequency**: 1 minute
- **Alert threshold**: Result count > 0
- **Action group**: Calls the self-healing Function webhook

### SLO Compliance Dashboard

File: `monitoring/kql-queries/slo-dashboard.kql`

Pin the availability SLO query as a dashboard tile in Azure Workbooks. The query calculates:

- **Availability %** per service over a rolling 30-day window
- **SLO compliant / breach** status against the 99.9% target
- **Error budget remaining** in minutes (43.2 minutes/month at 99.9% SLO)
- **Burn rate** (1-hour and 6-hour) for multi-window alerting

---

## Phase 3: Self-Healing Loop

The self-healing system closes the observability loop: Application Insights detects a problem, Azure Monitor fires an alert, and the Azure Function automatically remediates it.

### How It Works

```
1. Payment service begins returning 5xx errors (chaos mode on, or real failure)
         │
         ▼
2. KQL alert query detects ErrorRate > 10% over 5-minute window
         │
         ▼
3. Azure Monitor fires alert → calls Action Group webhook
         │
         ▼
4. Action Group sends HTTP POST to Azure Function (RestartContainer)
         │
         ▼
5. Function parses Common Alert Schema payload
         │
         ├── alert_name == "HighErrorRate"     → restart container app revision
         ├── alert_name == "PaymentServiceDown" → POST /circuit-breaker/open
         └── alert_name == "ContainerCrashLoop" → restart container app revision
         │
         ▼
6. azure-mgmt-appcontainers SDK restarts the affected revision
         │
         ▼
7. Function logs SELF_HEALING_ACTION trace to Application Insights
         │
         ▼
8. Service recovers; alert resolves; error rate returns to normal
```

### Configuring the Action Group

```bash
# Create the action group that calls your Function
az monitor action-group create \
  --name ag-self-healing \
  --resource-group rg-self-healing-dev \
  --short-name selfheal \
  --action azurefunction \
    self-healing-fn \
    /subscriptions/<SUB_ID>/resourceGroups/rg-self-healing-dev/providers/Microsoft.Web/sites/<FUNC_APP_NAME> \
    RestartContainer \
    https://<FUNC_APP_NAME>.azurewebsites.net/api/RestartContainer?code=<FUNCTION_KEY> \
    Common
```

### Deploying the Self-Healing Function

```bash
cd self-healing/azure-function

# Install dependencies locally
pip install -r requirements.txt

# Deploy to Azure
func azure functionapp publish <FUNC_APP_NAME>

# Set required application settings
az functionapp config appsettings set \
  --name <FUNC_APP_NAME> \
  --resource-group rg-self-healing-dev \
  --settings \
    AZURE_SUBSCRIPTION_ID="<SUB_ID>" \
    RESOURCE_GROUP_NAME="rg-self-healing-dev" \
    PAYMENT_SERVICE_URL="https://<payment-service-fqdn>"
```

### Testing the Self-Healing Function Manually

```bash
FUNC_URL="https://<FUNC_APP_NAME>.azurewebsites.net/api/RestartContainer?code=<KEY>"

curl -X POST $FUNC_URL \
  -H "Content-Type: application/json" \
  -d '{
    "schemaId": "azureMonitorCommonAlertSchema",
    "data": {
      "essentials": {
        "alertRule": "HighErrorRate",
        "alertId": "test-alert-001",
        "severity": "Sev2",
        "monitorCondition": "Fired",
        "firedDateTime": "2024-01-15T10:00:00Z",
        "configurationItems": [
          "/subscriptions/<SUB_ID>/resourceGroups/rg-self-healing-dev/providers/Microsoft.App/containerApps/payment-service"
        ]
      },
      "alertContext": {
        "correlationId": "test-correlation-001"
      }
    }
  }'
```

---

## Environment Variables

### Frontend

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: `3000`) |
| `ORDER_SERVICE_URL` | Yes | Base URL of the Order Service |
| `OTEL_SERVICE_NAME` | Yes | Service name reported to OpenTelemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Yes | OTLP collector URL (Jaeger or Azure Monitor) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Azure only | Application Insights connection string |

### Order Service

| Variable | Required | Description |
|----------|----------|-------------|
| `ASPNETCORE_URLS` | No | Listening address (default: `http://+:8080`) |
| `PAYMENT_SERVICE_URL` | Yes | Base URL of the Payment Service |
| `OTEL_SERVICE_NAME` | Yes | Service name reported to OpenTelemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Yes | OTLP collector URL |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Azure only | Application Insights connection string |

### Payment Service

| Variable | Required | Description |
|----------|----------|-------------|
| `PAYMENT_SERVICE_PORT` | No | Uvicorn port (default: `8000`) |
| `CHAOS_MODE_ENABLED` | No | Start with chaos mode active (`true`/`false`) |
| `CIRCUIT_BREAKER_ENABLED` | No | Enable circuit breaker logic (`true`/`false`) |
| `CIRCUIT_BREAKER_THRESHOLD` | No | Failure count before opening circuit (default: `5`) |
| `CIRCUIT_BREAKER_TIMEOUT_SECONDS` | No | Seconds before transitioning to half-open (default: `30`) |
| `OTEL_SERVICE_NAME` | Yes | Service name reported to OpenTelemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Yes | OTLP collector URL |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Azure only | Application Insights connection string |

### Self-Healing Function

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_SUBSCRIPTION_ID` | Yes | Azure subscription containing the Container Apps |
| `RESOURCE_GROUP_NAME` | Yes | Resource group containing the Container Apps |
| `PAYMENT_SERVICE_URL` | Yes | Base URL for circuit breaker toggle calls |
| `DEFAULT_CONTAINER_APP_NAME` | No | Fallback app name if alert payload lacks resource URI |

---

## Chaos Testing Guide

The payment service has a built-in chaos mode that introduces controlled failures to validate the self-healing loop.

### Enabling Chaos Mode

```bash
# Get the payment service URL
PAYMENT_URL="https://$(az containerapp show \
  --name payment-service \
  --resource-group rg-self-healing-dev \
  --query properties.configuration.ingress.fqdn -o tsv)"

# Enable chaos mode (payment service starts returning 503 errors)
curl -X POST $PAYMENT_URL/circuit-breaker/open \
  -H "Content-Type: application/json" \
  -d '{"triggered_by": "manual-test"}'

# Confirm chaos mode is active
curl $PAYMENT_URL/health
# Expected: {"status": "chaos_mode", "circuit_breaker": "open"}
```

### Generating Load to Trigger an Alert

```bash
FRONTEND_URL="https://$(az containerapp show \
  --name frontend \
  --resource-group rg-self-healing-dev \
  --query properties.configuration.ingress.fqdn -o tsv)"

# Send 100 requests over 2 minutes to exceed the alert threshold
for i in $(seq 1 100); do
  curl -s -X POST $FRONTEND_URL/api/order \
    -H "Content-Type: application/json" \
    -d "{\"item\": \"test-item-$i\", \"quantity\": 1, \"price\": 9.99}" &
  sleep 1.2
done
```

### What to Observe in Application Insights

1. **Transaction Search**: Within 2 minutes, you should see requests to `payment-service` failing with status 503.
2. **Failures blade**: The payment-service failure rate spikes above 10%.
3. **Azure Monitor Alerts**: The `HighErrorRate` alert fires and transitions to the **Fired** state.
4. **Self-Healing Function logs**: In Application Insights > Traces, filter by `cloud_RoleName == "self-healing-function"`. Look for `SELF_HEALING_ACTION` trace messages showing the restart.
5. **Recovery**: Within 1-2 minutes of the Function executing, the payment-service revision restarts and error rates return to baseline.

### Disabling Chaos Mode Manually

```bash
# Manually close the circuit breaker without waiting for self-healing
curl -X POST $PAYMENT_URL/circuit-breaker/close \
  -H "Content-Type: application/json" \
  -d '{"triggered_by": "manual-recovery"}'
```

### Chaos Test Scenarios

| Scenario | How to Trigger | Expected Self-Healing Action |
|----------|----------------|------------------------------|
| Payment service 503 flood | `POST /circuit-breaker/open` | Restart payment-service revision |
| High P95 latency | Set `PAYMENT_DELAY_MS=2000` env var | Restart payment-service revision |
| Container crash loop | Kill the process inside the container | ACA auto-restarts; Function sends alert |
| Order service dependency failure | Set `PAYMENT_SERVICE_URL` to invalid host | Circuit breaker opens; Function logs action |

---

## Application Map

The Application Map in Application Insights automatically builds a real-time topology of your services based on dependency telemetry.

### Accessing the Map

1. Open the Azure Portal and navigate to your Application Insights resource.
2. Select **Application Map** from the left navigation.
3. Allow 5-10 minutes after initial deployment for enough telemetry to populate the map.

### Reading the Map

- **Nodes** represent services (`frontend`, `order-service`, `payment-service`).
- **Edges** represent HTTP calls between services, labeled with average latency and call volume.
- **Red edges or nodes** indicate elevated failure rates. Click a node to see a breakdown by operation name and result code.
- **Drill-through**: Click any edge or node and select **Investigate Failures** or **Investigate Performance** to open filtered queries in the Logs blade.

### Correlating with Self-Healing Events

After a chaos test and self-healing cycle, use this query to overlay restart events with the error spike:

```kql
let restarts = traces
    | where timestamp > ago(2h)
    | where cloud_RoleName == "self-healing-function"
    | where message contains "SELF_HEALING_ACTION"
    | project timestamp, event = "Self-Healing Restart";
let errors = requests
    | where timestamp > ago(2h)
    | where cloud_RoleName == "payment-service"
    | where success == false
    | summarize FailedRequests = count() by bin(timestamp, 1m);
errors
| join kind=leftouter restarts on timestamp
| project timestamp, FailedRequests, SelfHealingEvent = event
```

---

## Deployment to Azure

### Full Deployment Steps

```bash
# 1. Set your variables
RESOURCE_GROUP="rg-self-healing-dev"
LOCATION="eastus"
ACR_NAME="acrselfhealingdev"

# 2. Create resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# 3. Deploy infrastructure
az deployment group create \
  --resource-group $RESOURCE_GROUP \
  --template-file infrastructure/bicep/main.bicep \
  --parameters @infrastructure/bicep/parameters/dev.parameters.json

# 4. Log in to ACR and push images
az acr login --name $ACR_NAME

docker build -t $ACR_NAME.azurecr.io/self-healing-frontend:latest services/frontend/
docker build -t $ACR_NAME.azurecr.io/self-healing-order-service:latest services/order-service/
docker build -t $ACR_NAME.azurecr.io/self-healing-payment-service:latest services/payment-service/

docker push $ACR_NAME.azurecr.io/self-healing-frontend:latest
docker push $ACR_NAME.azurecr.io/self-healing-order-service:latest
docker push $ACR_NAME.azurecr.io/self-healing-payment-service:latest

# 5. Update Container Apps with the pushed images
az containerapp update --name frontend --resource-group $RESOURCE_GROUP \
  --image $ACR_NAME.azurecr.io/self-healing-frontend:latest

az containerapp update --name order-service --resource-group $RESOURCE_GROUP \
  --image $ACR_NAME.azurecr.io/self-healing-order-service:latest

az containerapp update --name payment-service --resource-group $RESOURCE_GROUP \
  --image $ACR_NAME.azurecr.io/self-healing-payment-service:latest

# 6. Deploy the self-healing function
cd self-healing/azure-function
func azure functionapp publish <FUNC_APP_NAME>
```

### OIDC Setup for GitHub Actions

The CI/CD workflows use OIDC (federated identity) instead of long-lived credentials. Set up a federated credential on an App Registration:

```bash
# Create app registration
APP_ID=$(az ad app create --display-name "self-healing-github-actions" --query appId -o tsv)

# Create service principal
az ad sp create --id $APP_ID

# Assign Contributor role on the resource group
az role assignment create \
  --assignee $APP_ID \
  --role Contributor \
  --scope /subscriptions/<SUB_ID>/resourceGroups/rg-self-healing-dev

# Add federated credential for each workflow
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<your-org>/<your-repo>:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

Then add these secrets to your GitHub repository:

| Secret | Value |
|--------|-------|
| `AZURE_CLIENT_ID` | App Registration Application (client) ID |
| `AZURE_TENANT_ID` | Azure Active Directory tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `ACR_NAME` | Container Registry name (without `.azurecr.io`) |
| `RESOURCE_GROUP` | `rg-self-healing-dev` |
| `CONTAINER_APP_NAME` | Name of the specific Container App for each workflow |

---

## CI/CD Workflows

| Workflow | File | Trigger |
|----------|------|---------|
| Frontend | `.github/workflows/frontend.yml` | Push/PR on `services/frontend/**` |
| Order Service | `.github/workflows/order-service.yml` | Push/PR on `services/order-service/**` |
| Payment Service | `.github/workflows/payment-service.yml` | Push/PR on `services/payment-service/**` |
| Infrastructure | `.github/workflows/infrastructure.yml` | Push/PR on `infrastructure/**` |

Each service workflow follows three stages:

1. **Build and Test**: Installs dependencies and runs the test suite. PRs fail here if tests do not pass.
2. **Docker Build and Push**: Builds the container image tagged with the short commit SHA and pushes to ACR. Only runs on `push` events (not PRs).
3. **Deploy to Azure Container Apps**: Updates the Container App with the new image. Only runs when the push is to the `main` branch and uses the `production` environment for approval gates.

The infrastructure workflow adds a what-if analysis step that posts the proposed resource changes as a comment on pull requests, giving reviewers visibility into infrastructure impact before merge.

---

## Contributing

1. Fork the repository and create a branch from `main`: `git checkout -b feature/your-feature-name`
2. Make your changes. Each service is independently buildable and testable.
3. Test locally using `docker compose up --build` and verify traces appear in Jaeger at http://localhost:16686.
4. For KQL query changes, validate the query syntax in Application Insights > Logs before committing.
5. For Bicep changes, run `az bicep lint --file infrastructure/bicep/main.bicep` before pushing.
6. Open a pull request against `main`. The infrastructure workflow will post a what-if analysis on your PR automatically.
7. All CI checks must pass before merging. The `production` environment requires manual approval for deployments.

### Project Structure

```
Self-Healing-Microservices/
├── .github/
│   └── workflows/
│       ├── frontend.yml
│       ├── order-service.yml
│       ├── payment-service.yml
│       └── infrastructure.yml
├── infrastructure/
│   └── bicep/
│       ├── main.bicep
│       ├── modules/
│       └── parameters/
├── monitoring/
│   ├── alerts/
│   └── kql-queries/
│       ├── error-rate.kql
│       ├── p95-latency.kql
│       ├── service-health.kql
│       └── slo-dashboard.kql
├── self-healing/
│   └── azure-function/
│       ├── RestartContainer/
│       │   ├── __init__.py
│       │   └── function.json
│       ├── host.json
│       └── requirements.txt
├── services/
│   ├── frontend/
│   │   ├── src/
│   │   │   └── tracing.js
│   │   └── package.json
│   ├── order-service/
│   │   └── OrderService/
│   └── payment-service/
│       ├── app/
│       └── requirements.txt
├── docker-compose.yml
└── README.md
```
