using './main.bicep'

param existingWebAppName = 'ai-triage-agentic-system'
param location = 'swedencentral'
param environmentName = 'demo'
param smsPublishEnabled = false
param smsEnabled = false
param smsDailyLimit = 100
param notificationRetentionDays = 90
param smsSender = 'ALTER'
