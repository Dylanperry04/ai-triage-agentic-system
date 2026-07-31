# Tenant-only triage endpoint (Microsoft Entra ID)

## Intended outcome

The canonical entry point is:

```text
https://<app-service-host>/triage
```

Azure App Service Authentication (Easy Auth) must authenticate the browser with
one concrete Microsoft Entra tenant **before** the request reaches FastAPI. The
application then validates the token's `tid` claim, maps an Entra app role (or an
approved group object ID) to an internal role, checks
`view_workflow_queue`, records the access decision, and redirects to the React
UI. Anonymous users, users from a different tenant, and signed-in users without
an operational role fail closed.

This is an identity-protected public HTTPS endpoint. It is **not** an Azure
Private Endpoint. A Private Endpoint is an optional additional network control
that needs VNet/private DNS/VPN design; it does not replace tenant identity.

## What Alison / UHL-HSE IT must supply

Do not use a guessed or personal Microsoft tenant. Obtain:

- the exact target Entra **tenant ID** (a UUID);
- an App Registration / Enterprise Application in that tenant, with an owner;
- approval to assign the app's roles to named users or security groups;
- if MFA is required, a tenant administrator to apply an Entra Conditional
  Access policy to the enterprise application;
- the Azure resource group and App Service name.

If the App Service subscription is not in the target tenant, the portal's
"current tenant" shortcut is not sufficient. The target tenant administrator
must create/consent to the registration and provide the client ID and secret (or
approve the appropriate cross-tenant enterprise-application arrangement).

## Deploy in this order

1. **Deploy this updated code first.** It contains the `/triage` route, tenant
   claim validation, role mapping, App Service sign-out, and startup preflight.
2. In the App Registration, add the six roles from
   `infrastructure/entra_app_roles.json` to the manifest's `appRoles` array.
   Existing manifest properties must be preserved.
3. In **Enterprise applications → the application → Users and groups**, assign
   each approved person/group one of the app roles. For the visit, use
   `triage_nurse`, `ed_doctor`, or `clinical_supervisor`; these have workflow
   queue access. App roles are preferred to raw group claims because their
   values map directly and avoid group-claim overage ambiguity.
4. In **App Service → Settings → Authentication → Add identity provider**:

   - Identity provider: **Microsoft**
   - Tenant type: **Workforce**
   - App registration type: **Single tenant**
   - Restrict access: **Require authentication**
   - Unauthenticated requests: **HTTP 302 Found redirect**
   - Token store: **Enabled**
   - Issuer: `https://login.microsoftonline.com/<TENANT_ID>/v2.0`

   Use the existing target-tenant App Registration where the portal offers that
   option. Confirm the redirect URI includes
   `https://<app-service-host>/.auth/login/aad/callback`.
5. Run the supplied hardening script from an Azure CLI session that can update
   the App Service. The identity provider must already exist:

   ```powershell
   ./scripts/configure_tenant_endpoint.ps1 `
     -ResourceGroup "<resource-group>" `
     -WebAppName "<app-service-name>" `
     -TenantId "<target-tenant-uuid>"
   ```

   If group claims are used instead of app roles, pass immutable group object
   IDs explicitly:

   ```powershell
   -GroupRoleMapJson '{"<group-object-id>":"clinical_supervisor"}'
   ```

   The script configures single-tenant issuer/audience restrictions, requires
   authentication and HTTPS, enables the token store, disables the demo role
   selector, sets `AUTH_PROVIDER=azure`, and stores `ENTRA_TENANT_ID` in App
   Service settings. It deliberately does not create an app registration,
   assign users, create secrets, or create Conditional Access policy.

## MFA / SSO

Entra sign-in provides SSO. MFA is controlled by the tenant, not by FastAPI.
For the UHL visit, have the Entra administrator target this enterprise
application with a Conditional Access policy requiring an appropriate MFA
authentication strength. Use report-only/test assignments before broad rollout
and keep a documented break-glass account excluded according to local policy.

## Verification before presenting

First prove anonymous access is blocked:

```bash
python scripts/verify_tenant_endpoint.py \
  --base-url https://<app-service-host>
```

Then complete these browser checks in an InPrivate/incognito window:

- `/triage` redirects to Microsoft sign-in rather than exposing the UI.
- An assigned target-tenant user signs in and reaches the triage UI.
- `/auth/triage-link` returns `tenant_locked: true` and
  `tenant_validated: true` for that user.
- A personal Microsoft account or a user from a different tenant is denied.
- A target-tenant user with no assigned app role receives a 403 at `/triage`.
- The sign-out button clears the App Service session and returns to `/`.
- Azure sign-in logs show the test events; the app access audit shows the
  allowed/denied `tenant_triage_entry` decisions.

Do not present the endpoint as UHL tenant-restricted until the wrong-tenant and
unassigned-user checks have both passed.

## Data profile for the visit

The script uses the packaged synthetic supervisor-demo cases while replacing
the demo identity with real Entra authentication:

```text
AZURE_SUPERVISOR_DEMO_MODE=true
ALLOW_DEMO_ROLE_SWITCHER=false
PATIENT_DATA_MODE=false
```

This proves tenant-controlled access without claiming patient-data production
readiness. Do not set `PATIENT_DATA_MODE=true` for the visit unless Key Vault,
durable audit, approved data storage, network controls, governance and all other
production gates in `docs/DEPLOYMENT_SECURITY_CHECKLIST.md` are complete.

Credentialed full MIMIC on Azure additionally requires both explicit MIMIC
acknowledgement flags and the same valid Entra tenant configuration. It must
never be packaged into the deployment artefact.

## Recovery

Before changing Authentication, export/screenshot the current App Service auth
settings and retain a separate Azure resource owner who can recover the app.
If a configuration error locks out all users, that owner can correct the issuer,
registration, secret reference or role assignment in the Azure portal without
needing access to the application itself. Do not weaken authentication as a
routine workaround.

## Microsoft references

- [Configure Microsoft Entra sign-in for App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-provider-aad)
- [Authentication and authorization in Azure App Service](https://learn.microsoft.com/en-us/azure/app-service/overview-authentication-authorization)
- [Access identity claims in application code](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-user-identities)
- [App Service sign-out behaviour](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-customize-sign-in-out)
- [Require MFA with Conditional Access](https://learn.microsoft.com/en-us/entra/identity/conditional-access/policy-all-users-mfa-strength)
- [Azure CLI App Service authentication commands](https://learn.microsoft.com/en-us/cli/azure/webapp/auth?view=azure-cli-latest)
