using './main.bicep'

param existingWebAppName = 'Triage'
param existingCommunicationName = 'Alter'
param location = 'swedencentral'
param environmentName = 'demo'
param smsPublishEnabled = false
param smsEnabled = false
param smsDailyLimit = 100
param notificationRetentionDays = 90
param smsSender = 'ServiceSMS'
param messagingConnectApiKey = 'a7accca0916098b6081dbf6dc75aa865-cd2dc683-c096-4a9d-9470-c00a00f0e865'
