# Deployment Security Checklist — Before Any Non-Public-Data Use

This checklist must be completed by hospital IT / security **before** this
application is pointed at credentialed MIMIC or any UHL patient data. Until every
item is satisfied, the app remains a **public-data research demo** and is
configured to refuse patient-data mode (fail closed).

Owner column: **App** = handled in this repo; **IT** = hospital infrastructure/
identity team; **Both** = app integrates with an IT-provided capability.

## A. Identity & access
- [ ] **IT** — Microsoft Entra ID (Azure AD) app registration created for the app.
- [ ] **IT** — Entra authentication enabled in front of the app (Container Apps /
      App Service built-in auth), injecting `X-MS-CLIENT-PRINCIPAL`.
- [ ] **IT** — MFA enforced for all users of the app.
- [ ] **IT** — Conditional Access policy (managed/compliant device, location).
- [ ] **IT** — Entra groups created and mapped to roles: `triage-nurses`,
      `ed-doctors`, `clinical-supervisors`, `researchers`, `security-admins`,
      `governance-auditors`.
- [ ] **App** — `DEFAULT_GROUP_ROLE_MAP` matches the agreed Entra group names.
- [ ] **App** — `TRUSTED_AUTH_PROXY=true` set (only after Entra auth confirmed in front).
- [ ] **App** — `AUTH_PROVIDER` set to a non-demo value; demo role-switcher disabled.

## B. Network
- [ ] **IT** — Azure Container Apps **internal ingress** (not external) configured.
- [ ] **IT** — Private endpoint / VNet integration so the app is not publicly reachable.
- [ ] **IT** — Hospital VPN / private network access path for authorised users.
- [ ] **IT** — Hospital-managed device requirement enforced (Intune / compliance).

## C. Secrets & identity for services
- [ ] **IT** — Azure Key Vault provisioned; app granted access via Managed Identity.
- [ ] **IT** — Secrets stored in Key Vault: pseudonymisation salt, Azure OpenAI key.
- [ ] **App** — `SECRETS_PROVIDER=keyvault` and `KEY_VAULT_URL` /
      `AZURE_KEY_VAULT_URL` configured so the app builds a real
      `SecretClient` with managed identity.
- [ ] **App** - In `PATIENT_DATA_MODE` with `SECRETS_PROVIDER=keyvault`, do not
      set plain env `PSEUDONYM_SECRET`; the runtime pseudonym secret must be
      retrieved from Key Vault unless an explicit dev-test override is set.
- [ ] **App** — `PSEUDONYM_SECRET` sourced from Key Vault (not the dev fallback).

## D. Audit & data protection
- [ ] **IT** — Encrypted durable audit store provisioned (Azure Table / Blob /
      Cosmos / Log Analytics) with retention policy.
- [ ] **App** — `AUDIT_SINK=durable` with either
      `AZURE_AUDIT_TABLE_CONNECTION_STRING` + `AZURE_AUDIT_TABLE_NAME`, or
      `AZURE_AUDIT_TABLE_ENDPOINT` + `AZURE_AUDIT_TABLE_NAME` using managed
      identity. Confirm a real write/read probe in Azure.
- [ ] **App** — Confirm access-audit and workflow logs contain no raw identifiers
      (redaction + `assert_no_raw_identifiers` on the write path).
- [ ] **Both** — Confirm pseudonymous `case_uid` is used everywhere a case is logged.

## E. Patient-data mode
- [ ] **App** — `PATIENT_DATA_MODE=true` and `AUTH_REQUIRED=true` set.
- [ ] **App** — Verify the app fails closed (refuses access) with no trusted identity.
- [ ] **Both** — Verify a user with no mapped role gets **no** permissions.

## F. Data governance
- [ ] **IT/Research** — DUA in place for credentialed MIMIC; UHL data governance
      approval for any UHL data.
- [ ] **App** — Full MIMIC read from `MIMIC_FULL_ED_DIR` on the credentialed
      environment; confirm it is **never** copied into the repo or a build artifact.
- [ ] **Both** — Sign-off that the model is research-only and clinician review is
      required on every output (no autonomous triage).

## G. Assurance
- [ ] **IT/Security** — Independent security review / penetration test.
- [ ] **Both** — Review the role→permission matrix against actual clinical workflow.
- [ ] **Clinical** — Clinical safety review of the provisional MTS-style display
      and the under-triage characteristics of the selected model.

---

**Gate:** Patient data may be used only when sections A–F are fully checked and
section G has sign-off. Any unchecked item in A–E means the app must remain in
public-data demo mode.

---

## H. Server-side boundary & abuse protection (added v12)
- [ ] **App** — FastAPI routes enforce `requires(permission)` (verified by
      tests/test_api_auth_boundary.py).
- [ ] **App** — API startup guard active: refuses patient-data mode without the
      full secure config.
- [ ] **App** — Streamlit reads `st.context.headers` so Entra identity resolves
      in the UI; verify behind the real proxy.
- [ ] **App** — Pseudonym salt refuses the dev fallback in patient-data mode.
- [ ] **App** — Key Vault / durable audit fail loudly (raise) in patient-data
      mode if no client is wired.
- [ ] **App** — Chatbot rate limiting + max prompt length configured
      (CHATBOT_RATE_MAX_REQUESTS, CHATBOT_RATE_WINDOW_SECONDS,
      CHATBOT_MAX_PROMPT_CHARS).
- [ ] **IT** — For multi-instance, back rate limiting with a shared store (Redis).
- [ ] **IT** — Azure OpenAI quota/cost monitoring + alerts configured.

## I. Session & user-side controls (added v12)
- [ ] **IT** — Session timeout / idle logout enforced at the identity provider.
- [ ] **IT** — No shared accounts; managed-device-only; automatic screen lock.
- [ ] **App** — Demo role-switcher confirmed disabled in patient-data mode.
- [ ] **Both** — Exports restricted by RBAC; identifiable export tightly held.
- [ ] **IT** — Joiner/leaver access removal + periodic access review on Entra groups.

## J. Audit retention & incident response (added v12)
- [ ] **IT** — Audit retention period defined; immutable/tamper-resistant where possible.
- [ ] **Both** — Confirmed audit contains no raw identifiers (pseudonymous case_uid).
- [ ] **Both** — Breach/incident response process documented; on-call contact named.
- [ ] **Both** — Access revocation process documented.
- [ ] **Research lead** — DUA-breach notification path for credentialed MIMIC defined.
