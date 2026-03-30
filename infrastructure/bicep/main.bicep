@description('Azure region for all resources.')
param location string = 'eastus'

@description('Name of the deployment environment (e.g. dev, staging, prod).')
param environmentName string

@description('Name of the Azure Container Registry. Leave empty to create a new one.')
param acrName string = ''

@description('Full image reference for the frontend container (e.g. myacr.azurecr.io/frontend:latest).')
param frontendImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Full image reference for the order-service container.')
param orderServiceImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Full image reference for the payment-service container.')
param paymentServiceImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Application Insights connection string. If empty, one will be created by the monitoring module.')
@secure()
param appInsightsConnectionString string = ''

// ---------------------------------------------------------------------------
// Monitoring stack — Log Analytics + Application Insights
// ---------------------------------------------------------------------------
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
  }
}

// Use the caller-supplied connection string when provided; otherwise fall back
// to the one provisioned by the monitoring module.
var resolvedAppInsightsConnectionString = empty(appInsightsConnectionString)
  ? monitoring.outputs.appInsightsConnectionString
  : appInsightsConnectionString

// ---------------------------------------------------------------------------
// Container Apps environment + all three services
// ---------------------------------------------------------------------------
module containerApps 'modules/container-apps.bicep' = {
  name: 'container-apps-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    workspaceId: monitoring.outputs.workspaceId
    appInsightsConnectionString: resolvedAppInsightsConnectionString
    acrName: acrName
    frontendImage: frontendImage
    orderServiceImage: orderServiceImage
    paymentServiceImage: paymentServiceImage
  }
}

// ---------------------------------------------------------------------------
// Self-healing alerting — metric / log alerts + action group
// ---------------------------------------------------------------------------
module selfHealing 'modules/self-healing.bicep' = {
  name: 'self-healing-${environmentName}'
  params: {
    location: location
    workspaceId: monitoring.outputs.workspaceId
    appInsightsId: monitoring.outputs.appInsightsId
    paymentServiceContainerAppName: containerApps.outputs.paymentServiceName
    paymentServiceResourceId: containerApps.outputs.paymentServiceResourceId
  }
}

// ---------------------------------------------------------------------------
// Self-healing Azure Function App — remediation executor
// ---------------------------------------------------------------------------
module functionApp 'modules/function-app.bicep' = {
  name: 'function-app-${environmentName}'
  params: {
    location: location
    environmentName: environmentName
    appInsightsConnectionString: resolvedAppInsightsConnectionString
    paymentServiceUrl: containerApps.outputs.paymentServiceUrl
    actionGroupId: selfHealing.outputs.actionGroupId
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Public HTTPS URL for the frontend Container App.')
output frontendUrl string = containerApps.outputs.frontendUrl

@description('Internal FQDN for the order-service Container App.')
output orderServiceUrl string = containerApps.outputs.orderServiceUrl

@description('Internal FQDN for the payment-service Container App.')
output paymentServiceUrl string = containerApps.outputs.paymentServiceUrl

@description('Application Insights instrumentation key.')
output applicationInsightsKey string = monitoring.outputs.instrumentationKey

@description('Default HTTPS hostname of the self-healing Function App.')
output functionAppUrl string = functionApp.outputs.functionAppUrl
