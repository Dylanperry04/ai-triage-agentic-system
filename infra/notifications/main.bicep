targetScope = 'resourceGroup'

@description('Existing, authoritative App Service name. This template does not redeploy its application content.')
param existingWebAppName string = 'Triage'

@description('Azure region for the notification resources.')
param location string = 'swedencentral'

@description('Deployment environment tag.')
@allowed(['demo', 'production'])
param environmentName string = 'demo'

@description('Enables web publication and Function outbox reconciliation. Keep false until the sender and secret are approved.')
param smsPublishEnabled bool = false

@description('Enables chargeable ACS submission. Must remain false until sender registration and a live-test approval are complete.')
param smsEnabled bool = false

@description('UTC activation watermark. Required before SMS publication is enabled.')
param smsActivatedAtUtc string = ''

@description('Comma-separated canary case UIDs. Use one case for the first handset test; * is an explicit post-canary expansion.')
param smsDemoCaseUidAllowlist string = ''

@description('Monotonic durable rollout-policy version. Configuration tooling replaces this bootstrap value before SMS activation.')
param smsRolloutPolicyVersion string = '00000000T000000000000000Z-bootstrap'

@minValue(1)
@maxValue(100)
param smsDailyLimit int = 100

@minValue(1)
@maxValue(365)
param notificationRetentionDays int = 90

@description('Exact approved alphanumeric sender ID. Provisioning the resource does not register the sender.')
@allowed(['ALTER'])
param smsSender string = 'ALTER'

var token = toLower(take(uniqueString(subscription().id, resourceGroup().id, 'alter-notifications'), 8))
var tags = {
  application: 'ALTER'
  component: 'notifications'
  environment: environmentName
  managedBy: 'bicep'
  dataClassification: 'pseudonymous-operational'
}
var notificationStorageName = take('stalternotif${token}', 24)
var functionStorageName = take('stalterfunc${token}', 24)
var serviceBusName = take('sb-alter-notify-${token}', 50)
var functionPlanName = 'asp-alter-notify-${token}'
var functionName = take('func-alter-notify-${token}', 60)
var communicationName = take('acs-alter-${token}', 63)
var keyVaultName = take('kv-alter-${token}', 24)
var workspaceName = take('log-alter-notify-${token}', 63)
var insightsName = take('appi-alter-notify-${token}', 260)
var webIdentityName = take('id-alter-web-${token}', 128)
var functionIdentityName = take('id-alter-worker-${token}', 128)
var systemTopicName = take('evgt-alter-sms-${token}', 50)
var notificationTableName = 'alternotifications'
var dispatchQueueName = 'alter-sms-dispatch'
var deliveryQueueName = 'alter-sms-delivery'
var deploymentContainerName = 'function-releases'

resource existingWebApp 'Microsoft.Web/sites@2024-04-01' existing = {
  name: existingWebAppName
}

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: webIdentityName
  location: location
  tags: tags
}

resource functionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: functionIdentityName
  location: location
  tags: tags
}

resource notificationStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: notificationStorageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowCrossTenantReplication: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      requireInfrastructureEncryption: true
      services: {
        blob: { enabled: true }
        file: { enabled: true }
        queue: { enabled: true }
        table: { enabled: true }
      }
    }
  }
}

resource notificationTableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: notificationStorage
  name: 'default'
}

resource notificationTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: notificationTableService
  name: notificationTableName
}

resource functionStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: functionStorageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowCrossTenantReplication: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      requireInfrastructureEncryption: true
      services: {
        blob: { enabled: true }
        file: { enabled: true }
        queue: { enabled: true }
        table: { enabled: true }
      }
    }
  }
}

resource functionBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: functionStorage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: functionBlobService
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: serviceBusName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    zoneRedundant: false
  }
}

resource dispatchQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: dispatchQueueName
  properties: {
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
    duplicateDetectionHistoryTimeWindow: 'P7D'
    enableBatchedOperations: true
    enableExpress: false
    lockDuration: 'PT5M'
    maxDeliveryCount: 10
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true
    requiresSession: false
  }
}

resource deliveryQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: deliveryQueueName
  properties: {
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P1D'
    duplicateDetectionHistoryTimeWindow: 'P7D'
    enableBatchedOperations: true
    enableExpress: false
    lockDuration: 'PT5M'
    maxDeliveryCount: 10
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true
    requiresSession: false
  }
}

resource communication 'Microsoft.Communication/communicationServices@2025-09-01' = {
  name: communicationName
  location: 'global'
  tags: tags
  properties: {
    dataLocation: 'Europe'
    disableLocalAuth: true
    linkedDomains: []
    publicNetworkAccess: 'Enabled'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    publicNetworkAccess: 'Enabled'
    softDeleteRetentionInDays: 90
    sku: {
      family: 'A'
      name: 'standard'
    }
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    retentionInDays: 90
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    DisableLocalAuth: true
    RetentionInDays: 90
    WorkspaceResourceId: logAnalytics.id
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource functionPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: functionPlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

module functionApp 'modules/function-flex.bicep' = {
  name: 'notification-function'
  params: {
    name: functionName
    location: location
    tags: tags
    appServicePlanId: functionPlan.id
    storageAccountName: functionStorage.name
    deploymentStorageContainerName: deploymentContainer.name
    identityId: functionIdentity.id
    identityClientId: functionIdentity.properties.clientId
    applicationInsightsName: appInsights.name
    maximumInstanceCount: 20
    instanceMemoryMB: 2048
    appSettings: {
      AZURE_CLIENT_ID: functionIdentity.properties.clientId
      NOTIFICATION_MANAGED_IDENTITY_CLIENT_ID: functionIdentity.properties.clientId
      ACS_ENDPOINT: 'https://${communication.name}.communication.azure.com'
      DEMO_SMS_RECIPIENT: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=alter-demo-sms-recipient)'
      NOTIFICATION_BACKEND: 'azure_table'
      NOTIFICATION_TABLE_ENDPOINT: notificationStorage.properties.primaryEndpoints.table
      NOTIFICATION_TABLE_NAME: notificationTableName
      NOTIFICATION_RETENTION_DAYS: string(notificationRetentionDays)
      SERVICEBUS_FQDN: '${serviceBus.name}.servicebus.windows.net'
      ServiceBusConnection__fullyQualifiedNamespace: '${serviceBus.name}.servicebus.windows.net'
      ServiceBusConnection__clientId: functionIdentity.properties.clientId
      ServiceBusConnection__credential: 'managedidentity'
      SmsDispatchQueueName: dispatchQueue.name
      SmsDeliveryQueueName: deliveryQueue.name
      SMS_DISPATCH_QUEUE: dispatchQueue.name
      SMS_DAILY_LIMIT: string(smsDailyLimit)
      SMS_ACTIVATED_AT_UTC: smsActivatedAtUtc
      SMS_DEMO_CASE_UID_ALLOWLIST: smsDemoCaseUidAllowlist
      SMS_ROLLOUT_POLICY_VERSION: smsRolloutPolicyVersion
      SMS_DISPATCH_MIN_AGE_SECONDS: '90'
      SMS_ENABLED: string(smsEnabled)
      SMS_PUBLISH_ENABLED: string(smsPublishEnabled)
      SMS_RECIPIENT_MODE: 'demo_allowlist'
      SMS_RETRY_BASE_SECONDS: '60'
      SMS_RETRY_MAX_ATTEMPTS: '3'
      SMS_SENDER: smsSender
      NOTIFICATION_WORKER_STALE_SECONDS: '180'
      'AzureWebJobs.sms_dispatch.Disabled': string(!smsEnabled)
    }
  }
}

// Built-in role IDs from Microsoft Azure RBAC documentation.
var storageBlobDataOwnerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
var storageQueueDataContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
var storageTableDataContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
var serviceBusSenderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
var serviceBusDataOwnerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '090c5cfd-751d-490a-894a-3ce6f1109419')
var keyVaultSecretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var monitoringMetricsPublisherRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
var contributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')

resource webTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(notificationStorage.id, webIdentity.id, storageTableDataContributorRole)
  scope: notificationStorage
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageTableDataContributorRole
  }
}

resource functionTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(notificationStorage.id, functionIdentity.id, storageTableDataContributorRole)
  scope: notificationStorage
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageTableDataContributorRole
  }
}

resource functionBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorage.id, functionIdentity.id, storageBlobDataOwnerRole)
  scope: functionStorage
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataOwnerRole
  }
}

resource functionQueueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorage.id, functionIdentity.id, storageQueueDataContributorRole)
  scope: functionStorage
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageQueueDataContributorRole
  }
}

resource functionHostTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorage.id, functionIdentity.id, storageTableDataContributorRole)
  scope: functionStorage
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageTableDataContributorRole
  }
}

resource webBusSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, webIdentity.id, serviceBusSenderRole)
  scope: serviceBus
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusSenderRole
  }
}

// The Functions Service Bus scale controller needs namespace/queue management
// read actions for accurate target-based scaling. Microsoft documents Data
// Owner (or an equivalent custom role) for identity-based connections. Scope
// is deliberately restricted to this notification namespace.
resource functionBusDataOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, functionIdentity.id, serviceBusDataOwnerRole)
  scope: serviceBus
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusDataOwnerRole
  }
}

resource functionSecretRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionIdentity.id, keyVaultSecretsUserRole)
  scope: keyVault
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

// ACS currently documents Contributor for Entra-authenticated SMS SDK use.
// Scope is restricted to this one communication resource.
resource functionCommunicationRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(communication.id, functionIdentity.id, contributorRole)
  scope: communication
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: contributorRole
  }
}

resource functionMonitoringRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, functionIdentity.id, monitoringMetricsPublisherRole)
  scope: appInsights
  properties: {
    principalId: functionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringMetricsPublisherRole
  }
}

resource smsSystemTopic 'Microsoft.EventGrid/systemTopics@2025-02-15' = {
  name: systemTopicName
  location: 'global'
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    source: communication.id
    topicType: 'Microsoft.Communication.CommunicationServices'
  }
}

resource eventGridBusSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(deliveryQueue.id, smsSystemTopic.id, serviceBusSenderRole)
  scope: deliveryQueue
  properties: {
    principalId: smsSystemTopic.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusSenderRole
  }
}

resource smsDeliverySubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2025-02-15' = {
  parent: smsSystemTopic
  name: 'acs-sms-delivery-to-servicebus'
  dependsOn: [
    eventGridBusSenderRole
  ]
  properties: {
    deliveryWithResourceIdentity: {
      identity: {
        type: 'SystemAssigned'
      }
      destination: {
        endpointType: 'ServiceBusQueue'
        properties: {
          resourceId: deliveryQueue.id
        }
      }
    }
    eventDeliverySchema: 'EventGridSchema'
    filter: {
      includedEventTypes: [
        'Microsoft.Communication.SMSDeliveryReportReceived'
      ]
      isSubjectCaseSensitive: false
    }
    retryPolicy: {
      eventTimeToLiveInMinutes: 1440
      maxDeliveryAttempts: 30
    }
  }
}

resource serviceBusDeadLetterAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'ALTER notification dead-letter backlog'
  location: 'global'
  tags: tags
  properties: {
    description: 'A notification message reached a Service Bus dead-letter queue and requires investigation.'
    severity: 1
    enabled: true
    scopes: [serviceBus.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    autoMitigate: false
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'DeadletteredMessages'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          metricName: 'DeadletteredMessages'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
        }
      ]
    }
    actions: []
  }
}

output existingWebAppName string = existingWebApp.name
output webIdentityId string = webIdentity.id
output webIdentityClientId string = webIdentity.properties.clientId
output functionAppName string = functionApp.outputs.name
output functionIdentityId string = functionIdentity.id
output notificationTableEndpoint string = notificationStorage.properties.primaryEndpoints.table
output notificationTableName string = notificationTable.name
output serviceBusFqdn string = '${serviceBus.name}.servicebus.windows.net'
output dispatchQueueName string = dispatchQueue.name
output deliveryQueueName string = deliveryQueue.name
output communicationEndpoint string = 'https://${communication.name}.communication.azure.com'
output keyVaultName string = keyVault.name
output demoRecipientSecretName string = 'alter-demo-sms-recipient'
output smsPublishEnabled bool = smsPublishEnabled
output smsEnabled bool = smsEnabled
output smsRolloutPolicyVersion string = smsRolloutPolicyVersion
