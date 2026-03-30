@description('Azure region for all resources.')
param location string

@description('Name of the deployment environment used as a naming suffix.')
param environmentName string

@description('Application Insights connection string for telemetry.')
@secure()
param appInsightsConnectionString string

@description('URL of the payment-service Container App (used as a remediation target).')
param paymentServiceUrl string

@description('Azure Subscription ID the Function App will operate in.')
param subscriptionId string = subscription().subscriptionId

@description('Resource group name the Function App will operate in.')
param resourceGroupName string = resourceGroup().name

@description('Default Container App name the self-healing function will restart.')
param defaultContainerAppName string = 'ca-payment-service'

@description('Resource ID of the Action Group to wire remediation alerts to this function.')
param actionGroupId string

// ---------------------------------------------------------------------------
// Storage Account — required backing store for Azure Functions runtime
// ---------------------------------------------------------------------------
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'stselfheal${uniqueString(resourceGroup().id, environmentName)}'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Compose the storage connection string from the primary key
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

// ---------------------------------------------------------------------------
// App Service Plan — Consumption (Y1 / Dynamic) for serverless billing
// ---------------------------------------------------------------------------
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: 'asp-selfheal-${environmentName}'
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true // required for Linux Consumption plans
  }
}

// ---------------------------------------------------------------------------
// Function App — Linux / Python 3.12 / Consumption plan
// ---------------------------------------------------------------------------
resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: 'func-selfheal-${environmentName}'
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    reserved: true
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.12'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: storageConnectionString
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'PAYMENT_SERVICE_URL'
          value: paymentServiceUrl
        }
        {
          name: 'AZURE_SUBSCRIPTION_ID'
          value: subscriptionId
        }
        {
          name: 'RESOURCE_GROUP_NAME'
          value: resourceGroupName
        }
        {
          name: 'DEFAULT_CONTAINER_APP_NAME'
          value: defaultContainerAppName
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Role Assignment — Contributor on the resource group
// Allows the function's managed identity to restart Container App revisions
// ---------------------------------------------------------------------------
var contributorRoleDefinitionId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

resource functionAppContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  // Scope the assignment to the current resource group
  name: guid(resourceGroup().id, functionApp.id, contributorRoleDefinitionId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleDefinitionId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Name of the provisioned Function App.')
output functionAppName string = functionApp.name

@description('Default HTTPS hostname of the Function App (without protocol).')
output functionAppUrl string = functionApp.properties.defaultHostName

@description('Managed identity principal ID of the Function App (for additional role assignments).')
output functionAppPrincipalId string = functionApp.identity.principalId
