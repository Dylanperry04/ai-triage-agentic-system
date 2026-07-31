[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$WebAppName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')]
    [string]$TenantId,

    [string]$ClientId = "",
    [string]$ClientSecretSettingName = "",
    [string]$GroupRoleMapJson = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-AzText {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $value = & az @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed: $($value -join [Environment]::NewLine)"
    }
    return ($value -join [Environment]::NewLine).Trim()
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI is not installed. Install it, run 'az login', and retry."
}

$null = Invoke-AzText extension add --name authV2 --upgrade --only-show-errors --output none
$null = Invoke-AzText webapp show --resource-group $ResourceGroup --name $WebAppName --only-show-errors --output none

$hostName = Invoke-AzText webapp show `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --query defaultHostName `
    --output tsv `
    --only-show-errors

if (-not $ClientId) {
    $ClientId = Invoke-AzText webapp auth microsoft show `
        --resource-group $ResourceGroup `
        --name $WebAppName `
        --query registration.clientId `
        --output tsv `
        --only-show-errors
}
if (-not $ClientId) {
    throw "No Microsoft Entra client ID is configured. Add the Microsoft identity provider in App Service > Authentication first, then rerun this script."
}

if (-not $ClientSecretSettingName) {
    $ClientSecretSettingName = Invoke-AzText webapp auth microsoft show `
        --resource-group $ResourceGroup `
        --name $WebAppName `
        --query registration.clientSecretSettingName `
        --output tsv `
        --only-show-errors
}
if (-not $ClientSecretSettingName) {
    throw "The Entra provider has no client-secret setting reference. Finish adding the Microsoft identity provider in the Azure portal first."
}

if ($ClientSecretSettingName -ne "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID") {
    $secretSettingFound = Invoke-AzText webapp config appsettings list `
        --resource-group $ResourceGroup `
        --name $WebAppName `
        --query "[?name=='$ClientSecretSettingName'].name | [0]" `
        --output tsv `
        --only-show-errors
    if (-not $secretSettingFound) {
        throw "App setting '$ClientSecretSettingName' is missing. Re-save the Microsoft identity provider in App Service > Authentication so Azure creates its secret reference."
    }
}

if ($GroupRoleMapJson) {
    try {
        $roleMap = $GroupRoleMapJson | ConvertFrom-Json
    }
    catch {
        throw "GroupRoleMapJson must be a JSON object whose keys are Entra group object IDs and values are app roles."
    }
    $allowedRoles = @(
        "triage_nurse", "ed_doctor", "clinical_supervisor",
        "researcher", "security_admin", "governance_auditor"
    )
    if ($roleMap -isnot [PSCustomObject]) {
        throw "GroupRoleMapJson must be a JSON object."
    }
    foreach ($entry in $roleMap.PSObject.Properties) {
        if (-not $allowedRoles.Contains([string]$entry.Value)) {
            throw "Unknown role '$($entry.Value)' in GroupRoleMapJson."
        }
    }
}

$issuer = "https://login.microsoftonline.com/$TenantId/v2.0"

$null = Invoke-AzText webapp auth microsoft update `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --client-id $ClientId `
    --client-secret-setting-name $ClientSecretSettingName `
    --tenant-id $TenantId `
    --issuer $issuer `
    --allowed-audiences $ClientId `
    --yes `
    --only-show-errors `
    --output none

$null = Invoke-AzText webapp auth update `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --enabled true `
    --unauthenticated-client-action RedirectToLoginPage `
    --redirect-provider AzureActiveDirectory `
    --require-https true `
    --enable-token-store true `
    --only-show-errors `
    --output none

$settings = @(
    "AUTH_REQUIRED=true",
    "AUTH_PROVIDER=azure",
    "TRUSTED_AUTH_PROXY=true",
    "ENTRA_TENANT_ID=$TenantId",
    "AZURE_SUPERVISOR_DEMO_MODE=true",
    "ALLOW_DEMO_ROLE_SWITCHER=false",
    "LOCAL_CREDENTIALED_RESEARCH=false",
    "PATIENT_DATA_MODE=false",
    "REAL_PATIENT_DATA=false"
)
if ($GroupRoleMapJson) {
    $settings += "ENTRA_GROUP_ROLE_MAP=$GroupRoleMapJson"
}

$appSettingsArgs = @(
    "webapp", "config", "appsettings", "set",
    "--resource-group", $ResourceGroup,
    "--name", $WebAppName,
    "--settings"
) + $settings + @("--only-show-errors", "--output", "none")
$null = Invoke-AzText @appSettingsArgs

$null = Invoke-AzText webapp config set `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --https-only true `
    --min-tls-version 1.2 `
    --ftps-state Disabled `
    --only-show-errors `
    --output none

$null = Invoke-AzText webapp restart `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --only-show-errors `
    --output none

$authState = Invoke-AzText webapp auth show `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --query "{enabled:platform.enabled,requireAuthentication:globalValidation.requireAuthentication,unauthenticatedAction:globalValidation.unauthenticatedClientAction,provider:globalValidation.redirectToProvider,issuer:identityProviders.azureActiveDirectory.registration.openIdIssuer}" `
    --output json `
    --only-show-errors

Write-Host "Tenant endpoint configured for https://$hostName/triage"
Write-Host $authState
Write-Host "Next: assign at least one Entra app role to each test user or group, then run scripts/verify_tenant_endpoint.py."
