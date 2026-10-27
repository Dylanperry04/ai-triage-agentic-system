# Deploy the verified 100-case triage release to both existing Azure apps

Status: Validated
Recipe: AZCLI / existing GitHub Actions App Service ZIP deployment
Approval: The user explicitly requested publishing all reviewed changes to
`Dylanperry04/ai-triage-agentic-system` and deploying to both pictured apps.
No new application infrastructure, subscription changes, data deletion or live
SMS activation are part of this release. EDTRIAGE requires a GitHub deployment
identity, scoped to Website Contributor on that app and Reader on its resource
group for notification-worker discovery. Its OIDC trust accepts only this
repository's main branch. No long-lived deployment password is created.

## 1. Application and release

- Existing FastAPI service serves the compiled React UI.
- Python 3.11 runtime and Linux dependencies must match the packaged build.
- Exactly 100 source-derived cases; 60 matched checks, 22 safety escalations,
  final acuity counts 1/59/27/8/5; cohort SHA-256
  `1fb5aad5fec098da3335065f7932da6e832104988b579d4c991209e5e561f691`.
- The full training CSV is retained in Git but excluded from deployment ZIPs.

## 2. Explicit existing targets from the user's screenshots

| App | Subscription | Resource group | Region |
|---|---|---|---|
| ai-triage-agentic-system | 3e06b774-e965-4c81-aa02-897ec6647dcd | Ai-triaging | Sweden Central |
| EDTRIAGE | 4b5b261f-4692-4c6f-afd6-18874e1a0487 | Triage | Sweden Central |

Validate names, resource IDs, hostnames, runtime and current deployment settings
against Azure before changing the apps. The second screenshot shows Python
3.12; align it to 3.11 if Azure confirms that configuration.

## 3. Preparation

- Inspect Git credentials, Azure sign-in and both existing app configurations.
- Adapt CI/CD with isolated matrix jobs, each testing and packaging the same
  pinned release, then deploying to its explicit target. A failure on one app
  does not cancel the other. Create a single rollback tag only after both pass.
- Retain safeguards for notification state and rollback. Do not activate SMS.
- Preserve existing secret values, workflow/audit state and resources.
- Avoid competing workflows deploying different packages to the same app.

## 4. Validation

- Review workflow and target-specific credentials and least-privilege scope.
- Parse workflow YAML and run affected tests and deployment/package checks.
- Confirm source and cohort/model hashes, compiled UI and Python compatibility.
- Confirm Git push access and Azure access to both exact resource IDs.

## 5. Deployment

- Publish the complete tested source changes without rewriting Git history.
- Use the existing GitHub Actions deployment path with the verified package.
- Deploy to both existing apps, then verify the deployed build/cohort identity.
- Align Python 3.11/startup and disable Oryx rebuilding. Use extracted ZIP
  deployment (`WEBSITE_RUN_FROM_PACKAGE=0`); Python run-from-package is not
  supported by App Service. Preserve existing LLM credentials and durable state.

## 6. Post-deployment verification and rollback

- Check HTTPS UI, /health, /ready, /status/uhl and /runtime/status on both apps.
- Confirm 100 cases and expected cohort hash; verify 60 matched source cases.
- Confirm notification safety/state remains consistent and no SMS is enabled.
- Retain prior commit/configuration identifiers for rollback.

## 7. Validation proof

Validation performed 2026-10-27 under the azure-validate skill:

- `az account list`, `az webapp show`, `az webapp config show` and whitelisted
  app-setting queries confirmed both exact subscriptions/resource IDs/hostnames.
  The university app is Python 3.11; EDTRIAGE is Python 3.12 and will be aligned.
- `az identity federated-credential list` and `az role assignment list` verified
  main-branch OIDC and app-scoped Website Contributor plus resource-group Reader.
  University deployment reuses `oidc-msi-9ee5`; EDTRIAGE uses its scoped identity.
  GitHub deployment identifier secrets were configured without logging values.
- `git push --dry-run` with the repository owner's explicit credential succeeded;
  GitHub API confirmed write/admin access. No branch was created by the dry run.
- `az functionapp list` found no notification Functions in either resource group.
  Deployment will force SMS off and verify that state.
- `az bicep build --file infra/notifications/main.bicep --stdout`: passed; this
  validates retained notification source, without provisioning infrastructure.
- Official `actionlint` v1.7.12: passed. `git diff --check`: passed.
- `pytest tests/test_notification_infrastructure.py tests/test_deployment_packaging.py
  tests/test_azure_smoke_test.py -q`: 47 passed. Tests cover both exact targets,
  distinct credentials, runtime configuration, rollback safety and cohort checks.
- `python scripts/azure_preflight_check.py`: PASS, including model load/contract
  and integrity pins. Local optional LLM credentials are absent as expected.
- `pnpm test`: 86 passed; `pnpm build`: passed.
- `python scripts/verify_demo_source.py --source <attached CSV.gz>`: all 100
  records exactly verified against 777,176 source rows; no values generated or
  altered; source/cohort/model hashes match their release pins.
- Prior full release verification: 1,349 backend tests passed (21 skipped).
  The GitHub Linux runner will rerun the complete backend/frontend suites before
  either app is deployed, and validate the exact post-cleanup runtime package.

No application infrastructure template is being deployed; resource-group
creation, policy/region changes and infrastructure what-if are not applicable.

## 8. Deployment results

Not deployed yet.
