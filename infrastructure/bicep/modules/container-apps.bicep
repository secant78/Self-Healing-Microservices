@description('Azure region for all resources.')
param location string

@description('Name of the deployment environment used as a naming suffix.')
param environmentName string

@description('Resource ID of the Log Analytics Workspace for ACA managed-environment integration.')
param workspaceId string

@description('Application Insights connection string injected as an environment variable into every container.')
@secure()
param appInsightsConnectionString string

@description('Name of an existing Azure Container Registry. Leave empty to create a new basic-SKU registry.')
param acrName string = ''

@description('Full image reference for the frontend container.')
param frontendImage string

@description('Full image reference for the order-service container.')
param orderServiceImage string

@description('Full image reference for the payment-service container.')
param paymentServiceImage string

// ---------------------------------------------------------------------------
// Derive names
// ---------------------------------------------------------------------------
var resolvedAcrName = empty(acrName) ? 'acrselfhealing${environmentName}' : acrName
var acrLoginServer = '${resolvedAcrName}.azurecr.io'

// ---------------------------------------------------------------------------
// Azure Container Registry (created only when acrName is not supplied)
// ---------------------------------------------------------------------------
resource newAcr 'Microsoft.ContainerRegistry/registries@2023-07-01' = if (empty(acrName)) {
  name: resolvedAcrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

// Reference an existing ACR when the caller provided a name.
resource existingAcr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = if (!empty(acrName)) {
  name: acrName
}

// Retrieve the admin password for the managed-environment registry credential.
var acrPassword = empty(acrName)
  ? newAcr.listCredentials().passwords[0].value
  : existingAcr.listCredentials().passwords[0].value

// ---------------------------------------------------------------------------
// Resolve Log Analytics shared key for the managed environment
// ---------------------------------------------------------------------------
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: last(split(workspaceId, '/'))
}

// ---------------------------------------------------------------------------
// ACA Managed Environment
// ---------------------------------------------------------------------------
resource managedEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'cae-selfhealing-${environmentName}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    registries: [
      {
        server: acrLoginServer
        username: resolvedAcrName
        passwordSecretRef: 'acr-password'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Container App: frontend
// ---------------------------------------------------------------------------
resource frontendApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'ca-frontend-${environmentName}'
  location: location
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
      registries: [
        {
          server: acrLoginServer
          username: resolvedAcrName
          passwordSecretRef: 'acr-password'
        }
      ]
      ingress: {
        external: true
        targetPort: 3000
        transport: 'http'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: frontendImage
          env: [
            {
              name: 'ORDER_SERVICE_URL'
              value: 'http://ca-order-service-${environmentName}'
            }
            {
              name: 'PORT'
              value: '3000'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container App: order-service (internal)
// ---------------------------------------------------------------------------
resource orderServiceApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'ca-order-service-${environmentName}'
  location: location
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
      registries: [
        {
          server: acrLoginServer
          username: resolvedAcrName
          passwordSecretRef: 'acr-password'
        }
      ]
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
        allowInsecure: true
      }
    }
    template: {
      containers: [
        {
          name: 'order-service'
          image: orderServiceImage
          env: [
            {
              name: 'PAYMENT_SERVICE_URL'
              value: 'http://ca-payment-service-${environmentName}'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container App: payment-service (internal)
// ---------------------------------------------------------------------------
resource paymentServiceApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'ca-payment-service-${environmentName}'
  location: location
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
      registries: [
        {
          server: acrLoginServer
          username: resolvedAcrName
          passwordSecretRef: 'acr-password'
        }
      ]
      ingress: {
        external: false
        targetPort: 8000
        transport: 'http'
        allowInsecure: true
      }
    }
    template: {
      containers: [
        {
          name: 'payment-service'
          image: paymentServiceImage
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '30'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Public HTTPS URL of the frontend Container App.')
output frontendUrl string = 'https://${frontendApp.properties.configuration.ingress.fqdn}'

@description('Internal FQDN of the order-service Container App.')
output orderServiceUrl string = orderServiceApp.properties.configuration.ingress.fqdn

@description('Internal FQDN of the payment-service Container App.')
output paymentServiceUrl string = paymentServiceApp.properties.configuration.ingress.fqdn

@description('Resource ID of the ACA Managed Environment.')
output managedEnvironmentId string = managedEnvironment.id

@description('Name of the payment-service Container App (used by self-healing module).')
output paymentServiceName string = paymentServiceApp.name

@description('Resource ID of the payment-service Container App (used by self-healing module).')
output paymentServiceResourceId string = paymentServiceApp.id
