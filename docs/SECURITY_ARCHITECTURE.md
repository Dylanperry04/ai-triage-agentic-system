# Security Architecture — AI Triage Decision-Support (Research Prototype)

**Status: research prototype on public/demo data. NOT a secured production system,
NOT clinically validated, NOT cleared for real patient data.** This document
states plainly what the application enforces itself versus what the hospital
(University Hospital Limerick / HSE) must provide before this could handle
confidential patient data. The single most important point:

> **App-level RBAC is the *second* layer of security, not the first. It only has
> meaning behind a real identity provider and a trusted network. The application
> must run behind hospital-provided authentication and network controls; on its
> own it is a public-data demo.**

---

## 1. The two-layer model

```
┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 1 — HOSPITAL-PROVIDED (NOT app code; must exist for patient data)│
│   • Hospital-managed device (Intune / compliant endpoint)              │
│   • Secure network: hospital VPN / private network                     │
│   • Azure Container Apps INTERNAL ingress + private endpoint           │
│   • Microsoft Entra ID (Azure AD) authentication                       │
│   • MFA + Conditional Access policies                                  │
│   • Azure Key Vault (secrets) + Managed Identity                       │
│   • Encrypted durable storage for audit logs (Table/Blob/Cosmos)       │
└──────────────────────────────────────────────────────────────────────┘
                                   │  verified identity (trusted headers)
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 2 — APPLICATION-ENFORCED (this repo)                             │
│   • Identity boundary: reads the verified user; FAILS CLOSED           │
│   • App-level RBAC: 6 roles, least-privilege permission matrix         │
│   • Agent Security Gateway: boundary control around the LLM agents     │
│   • Patient-data protection: pseudonymisation + log redaction          │
│   • Access audit: every allowed/denied action logged (no raw IDs)      │
└──────────────────────────────────────────────────────────────────────┘
```

If Layer 1 is absent, Layer 2 alone is **not** sufficient for patient data, and
the app is configured to refuse (fail closed) in patient-data mode.

---

## 2. What the application enforces (implemented in this repo)

### 2.1 Identity boundary (`app/security/identity.py`)
- A pluggable `AuthContextProvider` interface. The production implementation,
  `AzureTrustedHeaderProvider`, reads the authenticated principal injected by
  Azure Container Apps / App Service auth via the `X-MS-CLIENT-PRINCIPAL` header
  (Base64 claims JSON), extracts user id / email / name / groups, and maps Entra
  groups (or app roles) to internal roles.
- **Trust is conditional.** Incoming `X-MS-*` headers are trusted **only** when
  `TRUSTED_AUTH_PROXY=true`, which the deployment sets **only** when platform auth
  genuinely fronts the app. Without it, forged headers are ignored.
- **Fail-closed.** When `PATIENT_DATA_MODE=true` or `AUTH_REQUIRED=true`, no demo
  stub identity is permitted; if no verified identity exists, access is refused.
- A clearly-labelled **demo stub** identity exists for local public-data demos
  only (`AUTH_PROVIDER=demo`), and is disabled in patient-data mode.

### 2.2 App-level RBAC (`app/security/authz.py`)
Six roles with a least-privilege permission matrix:

| Permission | nurse | ed_doctor | clin_supervisor | researcher | security_admin | gov_auditor |
|---|---|---|---|---|---|---|
| view_case | ✓ | ✓ | ✓ | ✓ | | |
| view_clinical_content | ✓ | ✓ | ✓ | | | |
| run_assessment | ✓ | ✓ | ✓ | ✓ | | |
| submit_review | ✓ | ✓ | ✓ | | | |
| ask_chatbot | ✓ | ✓ | ✓ | ✓ | | |
| view_model_performance | | ✓ | ✓ | ✓ | | ✓ |
| view_audit_log | | | ✓ | | ✓ | ✓ |
| view_security_status | | | | | ✓ | |
| export_deidentified | | | | ✓ | | |
| export_identifiable | | | | | ✓ | |

Key least-privilege decisions: a **researcher** can export de-identified research
outputs only (never identifiable patient data) and cannot submit clinical reviews;
a **security_admin** sees security/config status and access logs but **not**
clinical patient content; a **clinical_supervisor** has clinical oversight
(including audit log) but is kept **separate** from infra/security admin.

### 2.3 Agent Security Gateway (`app/security/agent_gateway.py`)
The four LLM agents (Intake, Validation, SafetyReview, Explanation) are **read-only
explainers**. The ML model produces the acuity estimate *before* any agent runs;
agents only explain it. We therefore did **not** build a heavyweight inter-agent
security layer (it would be disproportionate). Instead the gateway controls the
**boundary** into and out of the agent system:
- **Authorisation** — the caller must hold `can_ask_chatbot`.
- **Prompt-injection screening** on the user's question before it reaches the model.
- **Evidence minimisation** — only the minimal explainer fields are passed in.
- **No-action invariant** — `assert_agents_have_no_action_tools()` runs at team
  build time and fails closed if any agent is ever given a tool that could act
  (write/alter a result). This is the enforced, testable version of "a security
  layer between agents": the property the design depends on (agents cannot change
  the result) cannot silently regress.
- **Output safety filter** — the deterministic forbidden-phrase filter screens
  agent output (no category assignment, diagnosis, or treatment).
- **Audit** — every agent call (allowed / denied / blocked) is logged.

### 2.4 Patient-data protection (`app/security/redaction.py`, `audit_sink.py`)
- **Pseudonymous case_uid** — a stable, non-reversible, salted token; logs
  reference cases by this, never by raw patient/visit identifiers.
- **Log redaction** — `redact_for_log()` drops identifier keys and scrubs
  free-text; `assert_no_raw_identifiers()` is a hard guard that raises if a raw
  identifier reaches a log. The access-audit write path runs through this.
- These run on de-identified demo data today, enforcing the **same discipline**
  that becomes mandatory for full MIMIC / real UHL data, so behaviour does not
  change when data sensitivity does.

### 2.5 Access audit (`app/security/access_audit.py`)
Every security-relevant action records: timestamp, user id, role(s), action,
page, pseudonymous case_uid, decision (ALLOWED/DENIED/BLOCKED), permission checked,
whether a demo identity was used, and the auth source. No raw identifiers.

---

## 3. What the hospital MUST provide (NOT in this repo)

These are infrastructure/identity-platform responsibilities. The app integrates
with them but cannot implement them, and must not be described as providing them.

1. **Microsoft Entra ID (Azure AD) authentication** in front of the app
   (Container Apps / App Service built-in auth), issuing the trusted principal
   header the app reads. Set `TRUSTED_AUTH_PROXY=true` only once this is in place.
2. **MFA and Conditional Access** — enforced at Entra, not in the app.
3. **Hospital-managed device** posture (Intune / compliant endpoint).
4. **Secure network** — hospital VPN / private network; **Azure Container Apps
   internal ingress + private endpoint** so the app is not publicly reachable.
   (A sibling project is reached over VPN at an `azurecontainerapps.io` internal
   URL — the same pattern applies here.)
5. **Azure Key Vault + Managed Identity** for secrets (pseudonymisation salt,
   Azure OpenAI keys). Wire `KeyVaultSecretsProvider` with a real client and set
   `SECRETS_PROVIDER=keyvault`.
6. **Encrypted durable audit storage** (Azure Table / Blob / Cosmos / Log
   Analytics). Wire `EncryptedDurableAuditSink` with a real client and set
   `AUDIT_SINK=durable`. Local JSONL is for the demo only and is **not durable**
   on Container Apps' ephemeral storage.
7. **Data Use Agreement compliance** for credentialed MIMIC and any UHL data
   (see §5).

> VPN/network access is **one** layer. Even on a private network, the app still
> requires identity, RBAC, audit logging, and data minimisation — which is why
> Layer 2 exists and why the app fails closed without Layer 1's identity.

---

## 4. Configuration flags (security-relevant)

| Env var | Default | Meaning |
|---|---|---|
| `AUTH_PROVIDER` | `demo` | `demo` enables the labelled role-switcher (public data only). |
| `PATIENT_DATA_MODE` | `false` | `true` ⇒ fail closed; no demo identity; real auth required. |
| `AUTH_REQUIRED` | `false` | `true` ⇒ refuse unauthenticated access even for non-patient data. |
| `TRUSTED_AUTH_PROXY` | unset | `true` only when platform auth genuinely fronts the app. |
| `SECRETS_PROVIDER` | `env` | `keyvault` to use Key Vault (deployment injects client). |
| `AUDIT_SINK` | `local` | `durable` to use encrypted durable storage. |
| `PSEUDONYM_SECRET` | dev salt | Local research env secret only. In `PATIENT_DATA_MODE` with `SECRETS_PROVIDER=keyvault`, the runtime secret must come from Key Vault; a plain env `PSEUDONYM_SECRET` is refused unless an explicit dev-test override is set. |

**Production patient-data profile (all required):**
`PATIENT_DATA_MODE=true`, `AUTH_REQUIRED=true`, `TRUSTED_AUTH_PROXY=true`,
`AUTH_PROVIDER` ≠ `demo`, `SECRETS_PROVIDER=keyvault`, `AUDIT_SINK=durable`,
behind Entra auth + private ingress + VPN.

---

## 5. Data handling (credentialed data)

- **Full MIMIC-IV-ED** is credentialed PhysioNet data under a DUA. It must **never**
  be copied into this repository, a build artifact, or any shared sandbox. The
  pipeline reads it from a path the credentialed user controls on their own
  environment (`MIMIC_FULL_ED_DIR`), and the build excludes it.
- The application must stay in **local credentialed research** or **secured
  patient-data** profiles for full MIMIC-IV-ED. Full MIMIC is credentialed
  PhysioNet data even though it is deidentified.
- The current accepted model path is the full-MIMIC safety-first comparison
  workflow (`ml_training/full_mimic/compare_models.py`) plus a reviewed
  `MIMIC_FULL_MODEL_PATH` artefact. It is **research evidence, not a clinical
  tool**, until external validation and governance approval are complete.

---

## 6. Honest limitations

- This is a **research prototype**. The security controls in Layer 2 are real and
  tested, but they are not a substitute for Layer 1 and have not undergone a
  hospital security review or penetration test.
- No clinical validation, no UHL ground-truth validation, no prospective
  evaluation. Clinician review is required on every output.
- The prompt-injection screen is a heuristic defence-in-depth measure, not a
  guarantee; the deterministic output filter remains the primary safety control.
- "Most useful missing field" and the model comparison are research signals on a
  tiny demo set, not clinical evidence.

---

## 7. Deployment-requirements checklist

See `docs/DEPLOYMENT_SECURITY_CHECKLIST.md` for the itemised checklist IT/security
must complete before any non-public-data deployment.

---

## 8. Update (v12): server-side FastAPI boundary, hardening, and abuse protection

This section documents controls added after the initial architecture.

### 8.1 Server-side security boundary (FastAPI)
The FastAPI backend (`app/main.py`, `app/api/`) is now the **server-side
enforcement boundary**. Every protected route declares a required permission via
`app/api/auth_dependencies.py::requires(permission)`, which:
- reads the verified identity from the real request headers
  (`X-MS-CLIENT-PRINCIPAL`) through the same pluggable identity layer,
- returns **401** if unauthenticated and **403** if the role lacks the permission
  (fail closed in patient-data mode),
- audits every decision (allowed/denied).

On startup the API **refuses to run** in patient-data mode unless all
preconditions hold (`AUTH_REQUIRED`, non-demo `AUTH_PROVIDER`,
`TRUSTED_AUTH_PROXY`, `SECRETS_PROVIDER=keyvault`, `AUDIT_SINK=durable`). This
means even if the Streamlit layer were bypassed, the API rejects unauthenticated
or unauthorised callers. Streamlit also now reads `st.context.headers` so the
same identity resolves in the UI.

### 8.2 Fail-loud hardening (no silent downgrade)
In patient-data mode:
- the **pseudonymisation salt** refuses the dev fallback — a real
  `PSEUDONYM_SECRET` (from Key Vault) is required, else it raises;
- the **Key Vault provider** raises if `SECRETS_PROVIDER=keyvault` but no client
  is wired (no silent fallback to a weaker source);
- the **durable audit sink** raises if `AUDIT_SINK=durable` but no client is
  wired (audit data is never silently dropped).

Additional Key Vault rule: in `PATIENT_DATA_MODE` with
`SECRETS_PROVIDER=keyvault`, runtime pseudonymisation must retrieve
`PSEUDONYM_SECRET` from Key Vault. A plain env `PSEUDONYM_SECRET` is refused in
that profile unless an explicit dev-test override is set.

### 8.3 Abuse protection (chatbot/agent boundary)
`app/security/rate_limit.py` adds, at the agent boundary:
- a **per-user sliding-window request limiter** (`CHATBOT_RATE_MAX_REQUESTS` per
  `CHATBOT_RATE_WINDOW_SECONDS`),
- a **max prompt length** (`CHATBOT_MAX_PROMPT_CHARS`),
- a **per-session case cap** and **repeated-block tracking** (so many blocked
  attempts can be flagged).
In-process storage suffices for a single instance; a multi-instance deployment
should back this with a shared store (e.g. Redis).

---

## 9. Environment-variable profiles

**Public research demo (supervisor demo; public/demo data only):**

| Var | Value |
|---|---|
| PATIENT_DATA_MODE | false |
| AUTH_REQUIRED | false |
| AUTH_PROVIDER | demo |
| TRUSTED_AUTH_PROXY | false |
| SECRETS_PROVIDER | env |
| AUDIT_SINK | local |
| PROVISIONAL_MTS_MODE | on |

**Secured research deployment (credentialed MIMIC / patient data):**

| Var | Value |
|---|---|
| PATIENT_DATA_MODE | true |
| AUTH_REQUIRED | true |
| AUTH_PROVIDER | azure |
| TRUSTED_AUTH_PROXY | true |
| SECRETS_PROVIDER | keyvault |
| AUDIT_SINK | durable |
| PSEUDONYM_SECRET | (from Key Vault) |
| MIMIC_FULL_ED_DIR | (secure path outside the repo) |
| CORS_ALLOWED_ORIGINS | https://&lt;approved-app-domain&gt; |

Never set `CORS_ALLOWED_ORIGINS=*`.

---

## 10. Session, audit retention, and incident response

### 10.1 User-side / session controls (hospital + app)
- Session timeout / idle logout (enforced at the identity provider / reverse
  proxy; the app honours the authenticated session it is given).
- No shared accounts; no public/unmanaged devices; managed-device policy via
  Conditional Access.
- Automatic screen lock (device policy).
- The demo role-switcher is **disabled** whenever `PATIENT_DATA_MODE=true`.
- Downloads/exports are restricted by RBAC (`export_deidentified` vs
  `export_identifiable`).
- Failed/blocked access attempts are audited; repeated blocks can be alerted.
- Joiner/leaver access removal and periodic (e.g. monthly) access review are run
  by hospital IT against the Entra groups.

### 10.2 Audit retention & integrity
- Audit records carry no raw identifiers (pseudonymous `case_uid` only).
- Production audit goes to **encrypted durable storage** with a defined
  **retention period** and, where available, immutable/tamper-resistant storage
  (e.g. append-only / WORM, or Log Analytics with retention).
- Who may read audit logs is governed by RBAC (`view_audit_log`:
  clinical_supervisor, security_admin, governance_auditor).

### 10.3 Incident response (process, owned by hospital + research lead)
- Defined breach/incident response process and on-call contact.
- Access revocation process (disable Entra account / remove group membership).
- Steps for suspected data exposure, including DUA-breach notification for
  credentialed MIMIC.
- Post-incident review and corrective actions.
- These are governance/process items; sign-off is tracked in
  `docs/DEPLOYMENT_SECURITY_CHECKLIST.md` (section G).

---

## 11. Update (v13): final two-service architecture + pseudonymous identifiers

### 11.1 Streamlit frontend + FastAPI backend (one enforcement path)
The final architecture separates the UI from enforcement:
- **Streamlit is the frontend only.** Protected actions (list/get cases, run
  assessment, explanation, review submission, follow-ups, audit, model
  performance, security status) go through `frontend/api_client.py` to the
  FastAPI backend.
- **FastAPI is the sole server-side enforcement boundary.** Every protected route
  enforces `requires(permission)` (401/403), audits the decision, redacts
  identifiers, and fails closed in patient-data mode — independently of the UI.
- **Two transports, one enforcement path.** With `FASTAPI_BASE_URL` set, the
  client calls a separate FastAPI service over HTTP (the real Azure two-service
  topology). Without it, the client uses an in-process ASGI transport against the
  same FastAPI app, so even the single-process demo executes the real routes
  (RBAC, audit, redaction) — there is no Streamlit-only bypass.
- Identity propagation: the Entra principal header is forwarded to the backend; in
  demo mode the UI-selected role is forwarded (and ignored by the backend in
  patient-data mode). In patient-data mode, sensitive reads also go through the
  backend.
- Local credentialed MIMIC research is stricter than the public demo: Streamlit
  must set `FASTAPI_BASE_URL` and call a separate loopback FastAPI backend.
  In-process fallback is refused unless the explicit
  `ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH=true` dev-test
  override is set.

### 11.2 Canonical case_uid-keyed API
External identifiers are the pseudonymous `case_uid` (`<dataset>~<hmac>`, URL-safe).
Routes: `GET /cases`, `GET /cases/{case_uid}`, `POST /cases/{case_uid}/assessments`,
`/explanations`, `/reviews`, `/followups`, plus `GET /health`, `/security/status`,
`/audit/events`, `/model/performance`. Raw `stay_id`/`subject_id` never appear in a
URL, response, audit record, review record, workflow record, export, or agent
evidence package — only inside internal processing. A central resolver
(`app/api/case_resolver.py`) maps a `case_uid` back to its case by recomputing each
case's pseudonymous id (the HMAC is one-way), scoped by dataset.

### 11.3 Reviewer identity (item C)
In patient-data mode the review form shows the reviewer identity from the
authenticated context (no role dropdown); the backend derives and records
`reviewer_user_id`, `reviewer_roles`, and `auth_source`, and requires
`override_reason` for a genuine override/uncertain decision.

### 11.4 Full-MIMIC scaffolding (item H) — runs only on the credentialed env
`ml_training/full_mimic/` contains schema verification, feature builder, training,
evaluation (under/over-triage, high-acuity recall, calibration), model-card and
dataset-card generators, and an artefact-compatibility check. Every script calls
`require_safe_environment()`, which requires `MIMIC_FULL_ED_DIR` (outside the repo),
requires `PATIENT_DATA_MODE=true`, rejects repo-local data and output paths, and
refuses to write anything resembling raw patient rows. The old
`scripts/load_full_mimic_ed.py` (which downloaded into `data/raw/`) is DISABLED.
Adapter/report tests use synthetic MIMIC-shaped fixtures only.

### 11.5 Requirements split (item N)
`requirements.txt` (app runtime), `requirements-dev.txt` (tests),
`requirements-ml.txt` (full-MIMIC training only), `requirements-azure.txt` (Key
Vault + durable audit clients for the secured profile). scikit-learn is pinned for
model-artefact compatibility.
