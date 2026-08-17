param(
    [Parameter(Mandatory = $false)][string]$ResourceGroup = 'Triage_System',
    [Parameter(Mandatory = $false)][string]$WebAppName = 'Triage',
    [Parameter(Mandatory = $false)][string]$DeploymentName = 'alter-notifications',
    [Parameter(Mandatory = $false)][ValidateSet('true', 'false')][string]$SmsPublishEnabled = 'false',
    [Parameter(Mandatory = $false)][ValidateRange(1, 100)][int]$SmsDailyLimit = 100,
    [Parameter(Mandatory = $false)][string]$SmsActivatedAtUtc = '',
    [Parameter(Mandatory = $false)][string]$SmsDemoCaseUidAllowlist = '',
    [Parameter(Mandatory = $false)][string]$SmsRolloutPolicyVersion = ''
)

$ErrorActionPreference = 'Stop'

$normalisedAllowlistParts = @(
    $SmsDemoCaseUidAllowlist -split ',' |
        ForEach-Object { $_.Trim() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique
)
if ($normalisedAllowlistParts -contains '*' -and $normalisedAllowlistParts.Count -ne 1) {
    throw 'SMS_DEMO_CASE_UID_ALLOWLIST cannot combine * with individual case identifiers.'
}
$SmsDemoCaseUidAllowlist = $normalisedAllowlistParts -join ','

if ([string]::IsNullOrWhiteSpace($SmsRolloutPolicyVersion)) {
    $timestampPrefix = [DateTimeOffset]::UtcNow.ToString("yyyyMMdd'T'HHmmssfffffff'Z'")
    $SmsRolloutPolicyVersion = "$timestampPrefix-$([Guid]::NewGuid().ToString('N'))"
}
if ($SmsRolloutPolicyVersion -notmatch '^[A-Za-z0-9._:-]{1,128}$') {
    throw 'SMS_ROLLOUT_POLICY_VERSION must be a safe 1-128 character identifier.'
}

if ($SmsPublishEnabled -eq 'true') {
    if ([string]::IsNullOrWhiteSpace($SmsActivatedAtUtc) -or [string]::IsNullOrWhiteSpace($SmsDemoCaseUidAllowlist)) {
        throw 'SMS publication requires both -SmsActivatedAtUtc and -SmsDemoCaseUidAllowlist. Configure one post-cutover canary first; never enable an unbounded historical backlog.'
    }
    try {
        $activation = [DateTimeOffset]::Parse($SmsActivatedAtUtc).ToUniversalTime()
    } catch {
        throw '-SmsActivatedAtUtc must be an ISO-8601 timestamp with a timezone, for example 2026-08-14T12:00:00Z.'
    }
    if ($SmsActivatedAtUtc -notmatch '(Z|[+-]\d{2}:\d{2})$') {
        throw '-SmsActivatedAtUtc must include an explicit UTC offset.'
    }
    $SmsActivatedAtUtc = $activation.ToString('o')
}

$resolvedApp = az webapp show --resource-group $ResourceGroup --name $WebAppName --query name --output tsv
if ($LASTEXITCODE -ne 0 -or $resolvedApp -ne $WebAppName) {
    throw "Refusing to configure an unresolved or unexpected App Service target."
}

$outputsJson = az deployment group show --resource-group $ResourceGroup --name $DeploymentName --query properties.outputs --output json
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($outputsJson)) {
    throw "Could not read notification deployment outputs."
}
$outputs = $outputsJson | ConvertFrom-Json

$identityId = $outputs.webIdentityId.value
$clientId = $outputs.webIdentityClientId.value
$functionAppName = $outputs.functionAppName.value
if (
    [string]::IsNullOrWhiteSpace($identityId) -or
    [string]::IsNullOrWhiteSpace($clientId) -or
    [string]::IsNullOrWhiteSpace($functionAppName)
) {
    throw "Notification deployment did not return the web identity and Function app outputs."
}

$resolvedFunction = az functionapp show --resource-group $ResourceGroup --name $functionAppName --query name --output tsv
if ($LASTEXITCODE -ne 0 -or $resolvedFunction -ne $functionAppName) {
    throw "Refusing to configure an unresolved or unexpected notification Function target."
}

az webapp identity assign --resource-group $ResourceGroup --name $WebAppName --identities $identityId --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to attach the notification managed identity." }

$settings = @(
    "NOTIFICATION_MANAGED_IDENTITY_CLIENT_ID=$clientId",
    "NOTIFICATION_BACKEND=azure_table",
    "NOTIFICATION_TABLE_ENDPOINT=$($outputs.notificationTableEndpoint.value)",
    "NOTIFICATION_TABLE_NAME=$($outputs.notificationTableName.value)",
    "NOTIFICATION_RETENTION_DAYS=90",
    "SERVICEBUS_FQDN=$($outputs.serviceBusFqdn.value)",
    "SMS_DISPATCH_QUEUE=$($outputs.dispatchQueueName.value)",
    "SMS_DAILY_LIMIT=$SmsDailyLimit",
    "SMS_ACTIVATED_AT_UTC=$SmsActivatedAtUtc",
    "SMS_DEMO_CASE_UID_ALLOWLIST=$SmsDemoCaseUidAllowlist",
    "SMS_ROLLOUT_POLICY_VERSION=$SmsRolloutPolicyVersion",
    "SMS_DISPATCH_MIN_AGE_SECONDS=90",
    "SMS_ENABLED=false",
    "SMS_PUBLISH_ENABLED=$SmsPublishEnabled",
    "SMS_RECIPIENT_MODE=demo_allowlist",
    "SMS_SENDER=ALTER",
    "NOTIFICATION_WORKER_STALE_SECONDS=180"
)

az webapp config appsettings set --resource-group $ResourceGroup --name $WebAppName --settings $settings --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to configure notification App Settings." }

$functionSettings = @(
    "SMS_DAILY_LIMIT=$SmsDailyLimit",
    "SMS_ACTIVATED_AT_UTC=$SmsActivatedAtUtc",
    "SMS_DEMO_CASE_UID_ALLOWLIST=$SmsDemoCaseUidAllowlist",
    "SMS_ROLLOUT_POLICY_VERSION=$SmsRolloutPolicyVersion",
    "SMS_ENABLED=false",
    "SMS_PUBLISH_ENABLED=$SmsPublishEnabled",
    "AzureWebJobs.sms_dispatch.Disabled=true"
)
az functionapp config appsettings set --resource-group $ResourceGroup --name $functionAppName --settings $functionSettings --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to configure notification Function App Settings." }

$webPublish = az webapp config appsettings list --resource-group $ResourceGroup --name $WebAppName --query "[?name=='SMS_PUBLISH_ENABLED'].value | [0]" --output tsv
$webEnabled = az webapp config appsettings list --resource-group $ResourceGroup --name $WebAppName --query "[?name=='SMS_ENABLED'].value | [0]" --output tsv
$functionPublish = az functionapp config appsettings list --resource-group $ResourceGroup --name $functionAppName --query "[?name=='SMS_PUBLISH_ENABLED'].value | [0]" --output tsv
$functionEnabled = az functionapp config appsettings list --resource-group $ResourceGroup --name $functionAppName --query "[?name=='SMS_ENABLED'].value | [0]" --output tsv
$triggerDisabled = az functionapp config appsettings list --resource-group $ResourceGroup --name $functionAppName --query "[?name=='AzureWebJobs.sms_dispatch.Disabled'].value | [0]" --output tsv
$webDailyLimit = az webapp config appsettings list --resource-group $ResourceGroup --name $WebAppName --query "[?name=='SMS_DAILY_LIMIT'].value | [0]" --output tsv
$functionDailyLimit = az functionapp config appsettings list --resource-group $ResourceGroup --name $functionAppName --query "[?name=='SMS_DAILY_LIMIT'].value | [0]" --output tsv
$webActivatedAt = az webapp config appsettings list --resource-group $ResourceGroup --name $WebAppName --query "[?name=='SMS_ACTIVATED_AT_UTC'].value | [0]" --output tsv
$functionActivatedAt = az functionapp config appsettings list --resource-group $ResourceGroup --name $functionAppName --query "[?name=='SMS_ACTIVATED_AT_UTC'].value | [0]" --output tsv
$webAllowlist = az webapp config appsettings list --resource-group $ResourceGroup --name $WebAppName --query "[?name=='SMS_DEMO_CASE_UID_ALLOWLIST'].value | [0]" --output tsv
$functionAllowlist = az functionapp config appsettings list --resource-group $ResourceGroup --name $functionAppName --query "[?name=='SMS_DEMO_CASE_UID_ALLOWLIST'].value | [0]" --output tsv
$webPolicyVersion = az webapp config appsettings list --resource-group $ResourceGroup --name $WebAppName --query "[?name=='SMS_ROLLOUT_POLICY_VERSION'].value | [0]" --output tsv
$functionPolicyVersion = az functionapp config appsettings list --resource-group $ResourceGroup --name $functionAppName --query "[?name=='SMS_ROLLOUT_POLICY_VERSION'].value | [0]" --output tsv

if (
    $webPublish.ToLowerInvariant() -ne $SmsPublishEnabled -or
    $webEnabled.ToLowerInvariant() -ne 'false' -or
    $functionPublish.ToLowerInvariant() -ne $SmsPublishEnabled -or
    $functionEnabled.ToLowerInvariant() -ne 'false' -or
    $triggerDisabled.ToLowerInvariant() -ne 'true' -or
    [int]$webDailyLimit -ne $SmsDailyLimit -or
    [int]$functionDailyLimit -ne $SmsDailyLimit -or
    $webActivatedAt -ne $SmsActivatedAtUtc -or
    $functionActivatedAt -ne $SmsActivatedAtUtc -or
    $webAllowlist -ne $SmsDemoCaseUidAllowlist -or
    $functionAllowlist -ne $SmsDemoCaseUidAllowlist -or
    $webPolicyVersion -ne $SmsRolloutPolicyVersion -or
    $functionPolicyVersion -ne $SmsRolloutPolicyVersion
) {
    throw "Notification staging settings did not read back in the required fail-closed state."
}

$webHost = az webapp show --resource-group $ResourceGroup --name $WebAppName --query defaultHostName --output tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($webHost)) {
    throw 'Could not resolve the App Service hostname for durable-policy verification.'
}

$policyConverged = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $health = Invoke-RestMethod `
            -Method Get `
            -Uri "https://$webHost/notifications/system/health" `
            -Headers @{ 'X-Demo-Role' = 'security_admin'; 'X-Demo-User' = 'notification-configurator' } `
            -TimeoutSec 30
        if (
            $health.available -eq $true -and
            $health.rollout_policy.active_version -eq $SmsRolloutPolicyVersion -and
            $health.rollout_policy.configured_version -eq $SmsRolloutPolicyVersion -and
            $health.rollout_policy.version_match -eq $true
        ) {
            $policyConverged = $true
            break
        }
    } catch {
        # App Settings restart the application. A short connection or 503
        # failure is expected while the new process starts, so retry boundedly.
    }
    if ($attempt -lt 30) { Start-Sleep -Seconds 10 }
}
if (-not $policyConverged) {
    throw "Durable rollout policy $SmsRolloutPolicyVersion did not converge in the App Service health endpoint."
}

Write-Host "Configured $WebAppName and $functionAppName consistently and verified durable rollout policy $SmsRolloutPolicyVersion. SMS publishing is $SmsPublishEnabled; Function submission and its dispatch trigger remain disabled."
