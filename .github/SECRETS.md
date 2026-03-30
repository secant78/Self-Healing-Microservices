# GitHub Actions Secrets

This document lists every secret that must be configured in the repository
(**Settings > Secrets and variables > Actions**) before the CI/CD workflows
will run successfully.

---

## Required Secrets

| Secret | Description | Where to Find |
|--------|-------------|---------------|
| `AZURE_CLIENT_ID` | App Registration Client ID used for OIDC federated authentication | Azure Portal > App Registrations > your app > Overview |
| `AZURE_TENANT_ID` | Azure Active Directory Tenant ID | Azure Portal > Azure Active Directory > Overview |
| `AZURE_SUBSCRIPTION_ID` | Azure Subscription ID that hosts all project resources | Azure Portal > Subscriptions |
| `ACR_NAME` | Azure Container Registry name **without** the `.azurecr.io` suffix | Set during infrastructure deployment (Bicep output `acrName`) |
| `RESOURCE_GROUP` | Azure Resource Group name that contains all project resources | Set during infrastructure deployment |
| `FRONTEND_CONTAINER_APP` | Azure Container App name for the frontend service | Output of Bicep deployment (`frontendUrl` → app name) |
| `ORDER_SERVICE_CONTAINER_APP` | Azure Container App name for the order-service | Output of Bicep deployment (`orderServiceUrl` → app name) |
| `PAYMENT_SERVICE_CONTAINER_APP` | Azure Container App name for the payment-service | Output of Bicep deployment (`paymentServiceUrl` → app name) |

---

## Setting Up OIDC Federated Authentication

The workflows in this repository authenticate to Azure using **OpenID Connect
(OIDC) federation** — no long-lived client secrets are stored in GitHub.

### Prerequisites

- An Azure App Registration (service principal) with the **Contributor** role
  on the target subscription or resource group.
- The App Registration must have a **federated credential** that trusts tokens
  issued by GitHub Actions for this repository.

### Steps

1. Run the helper script to create the App Registration and federated
   credential automatically:

   ```bash
   bash infrastructure/scripts/setup.sh
   ```

   The script outputs the three values you need (`clientId`, `tenantId`,
   `subscriptionId`). Add them as the `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   and `AZURE_SUBSCRIPTION_ID` secrets.

2. If you prefer to configure manually, follow the
   [Azure OIDC federation guide](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure)
   and add a federated credential with:

   | Field | Value |
   |-------|-------|
   | Issuer | `https://token.actions.githubusercontent.com` |
   | Subject | `repo:<org>/<repo>:ref:refs/heads/main` (adjust branch as needed) |
   | Audience | `api://AzureADTokenExchange` |

3. Add all secrets listed in the table above to
   **GitHub > Settings > Secrets and variables > Actions > New repository secret**.

### How Workflows Use OIDC

Workflows request a short-lived token from GitHub's OIDC provider and exchange
it for an Azure access token using the `azure/login` action:

```yaml
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

No passwords or client secrets are ever stored or transmitted.
