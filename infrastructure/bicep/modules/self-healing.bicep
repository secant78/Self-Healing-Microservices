@description('Azure region for all resources.')
param location string

@description('Resource ID of the Log Analytics Workspace used for log-based alert queries.')
param workspaceId string

@description('Resource ID of the Application Insights component.')
param appInsightsId string

@description('Name of the payment-service Container App (used in alert dimensions).')
param paymentServiceContainerAppName string

@description('Resource ID of the payment-service Container App (used as the metric alert scope).')
param paymentServiceResourceId string

@description('Email address that receives alert notifications.')
param alertEmailAddress string = 'ops-team@example.com'

@description('Webhook URL for an Azure Function that performs automated remediation.')
param remediationWebhookUrl string = 'https://func-selfhealing.azurewebsites.net/api/remediate'

// ---------------------------------------------------------------------------
// Action Group — email + webhook receiver
// ---------------------------------------------------------------------------
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-selfhealing-payment'
  location: 'global'
  properties: {
    groupShortName: 'SH-Payment'
    enabled: true
    emailReceivers: [
      {
        name: 'OpsTeamEmail'
        emailAddress: alertEmailAddress
        useCommonAlertSchema: true
      }
    ]
    webhookReceivers: [
      {
        name: 'RemediationFunction'
        serviceUri: remediationWebhookUrl
        useCommonAlertSchema: true
        useAadAuth: false
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Metric Alert: payment-service HTTP failure rate > 10 %
// Uses the built-in requests/failed metric emitted by Container Apps via
// Application Insights (scope = App Insights resource).
// ---------------------------------------------------------------------------
resource paymentFailureRateAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-payment-failure-rate'
  location: 'global'
  properties: {
    description: 'Fires when the payment-service HTTP failure rate exceeds 10 % over a 5-minute window.'
    severity: 2
    enabled: true
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'FailureRateCriterion'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'requests/failed'
          metricNamespace: 'microsoft.insights/components'
          operator: 'GreaterThan'
          threshold: 10
          timeAggregation: 'Count'
          dimensions: [
            {
              name: 'cloud/roleName'
              operator: 'Include'
              values: [
                paymentServiceContainerAppName
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
        webHookProperties: {
          alertType: 'payment-failure-rate'
          service: paymentServiceContainerAppName
        }
      }
    ]
    autoMitigate: true
  }
}

// ---------------------------------------------------------------------------
// Metric Alert: payment-service P95 latency > 3000 ms
// ---------------------------------------------------------------------------
resource paymentLatencyAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-payment-p95-latency'
  location: 'global'
  properties: {
    description: 'Fires when the payment-service P95 request duration exceeds 3000 ms over a 5-minute window.'
    severity: 2
    enabled: true
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'P95LatencyCriterion'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'requests/duration'
          metricNamespace: 'microsoft.insights/components'
          operator: 'GreaterThan'
          threshold: 3000
          timeAggregation: 'Average'
          dimensions: [
            {
              name: 'cloud/roleName'
              operator: 'Include'
              values: [
                paymentServiceContainerAppName
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
        webHookProperties: {
          alertType: 'payment-p95-latency'
          service: paymentServiceContainerAppName
        }
      }
    ]
    autoMitigate: true
  }
}

// ---------------------------------------------------------------------------
// Scheduled Query (Log) Alert: payment-service 5xx rate > 10 % in 5 min
// Queries AppRequests in the Log Analytics workspace linked to App Insights.
// ---------------------------------------------------------------------------
resource paymentErrorRateLogAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-payment-500-error-rate'
  location: location
  properties: {
    description: 'Fires when the payment-service 5xx error rate exceeds 10 % of total requests over a 5-minute window.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      workspaceId
    ]
    targetResourceTypes: [
      'Microsoft.OperationalInsights/workspaces'
    ]
    criteria: {
      allOf: [
        {
          query: '''
            let totalRequests = AppRequests
              | where TimeGenerated >= ago(5m)
              | where AppRoleName == '${paymentServiceContainerAppName}'
              | summarize Total = count();
            let errorRequests = AppRequests
              | where TimeGenerated >= ago(5m)
              | where AppRoleName == '${paymentServiceContainerAppName}'
              | where ResultCode startswith "5"
              | summarize Errors = count();
            totalRequests
              | join kind=inner errorRequests on $left.$table == $right.$table
              | extend ErrorRate = todouble(Errors) / todouble(Total) * 100
              | where ErrorRate > 10
              | project ErrorRate, Errors, Total
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
      customProperties: {
        alertType: 'payment-500-error-rate'
        service: paymentServiceContainerAppName
      }
    }
    autoMitigate: false
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Resource ID of the Action Group used by all payment-service alerts.')
output actionGroupId string = actionGroup.id
