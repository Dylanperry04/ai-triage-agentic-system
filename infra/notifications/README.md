# ALTER notification infrastructure

This Bicep layer adds durable notification storage, Service Bus, an Azure
Functions worker, ACS, Event Grid delivery reports, Key Vault, and monitoring to
the existing `Triage` App Service in the `Triage_System` resource group and
reuses the existing `Alter` Azure Communication Services resource. It is based on Microsoft's pinned
`servicebus-trigger-python-azd` Python Functions template (`v1.0.0`).

Both `smsPublishEnabled` and `smsEnabled` default to `false`. The same-day demo
uses Infobip Messaging Connect with the trial sender `ServiceSMS`; the branded
`ALTER` sender can be switched in later after registration. Deployment does not
create the recipient secret, send a message, or enable chargeable SMS.

The supplied demo recipient must be entered after deployment through a secure
operator session, never through Bicep parameters or GitHub source:

```powershell
$secretValue = Read-Host 'Irish demo mobile in E.164 format' -AsSecureString
Set-AzKeyVaultSecret -VaultName '<output keyVaultName>' -Name 'alter-demo-sms-recipient' -SecretValue $secretValue
```

Run `scripts/configure-notification-app.ps1` after the Bicep deployment to attach
the dedicated web identity and configure non-secret App Service settings. Then
deploy the Function package. Sender registration, setting the recipient secret,
turning on the two feature flags, and a live handset test each require an
explicitly authorised operational step.
