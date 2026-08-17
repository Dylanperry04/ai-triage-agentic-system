# ACS SMS notification operations

## Safety boundary

The in-app notification is the authoritative workflow record. SMS is a
secondary prompt. Creating an alert and sending its SMS are deliberately
separate: an ACS, Service Bus, carrier, Function, cap, or recipient failure
cannot remove the in-app alert.

SMS content never includes a patient name, case identifier, MRN, complaint,
diagnosis, acuity, vital value, sepsis state, or patient link. The personal demo
recipient is held only in Key Vault secret `alter-demo-sms-recipient`; it must
not appear in source, Bicep parameters, GitHub secrets, application-created
dispatch messages, Application Insights, fixtures, or operator transcripts.

Microsoft-generated ACS delivery events are a necessary exception: Event Grid
includes the destination number in `data.to` and in the event subject. The
secured delivery queue and its DLQ can therefore contain the number. Access is
least-privilege, the worker never logs raw event bodies, active delivery events
expire after one day, and the DLQ must be explicitly cleaned after the demo.
Service Bus does not apply TTL while a message is in a DLQ.

## Event guarantees

- A notification ID is a deterministic hash of notification type, the already
  emitted pseudonymous case UID, and the immutable event clock.
- The notification record is committed before Service Bus publication.
- An outbox reconciler safely republishes records left between those two steps.
- If a dispatch invocation is already in flight while SMS is disabled, it
  atomically advances the record to a new unpublished generation before the old
  broker message completes. Re-enabling publication therefore cannot strand a
  `queued` record whose only Service Bus message was consumed.
- Service Bus duplicate detection is configured for seven days, and the worker
  independently claims the notification using optimistic concurrency.
- App Settings are operator input, not the in-flight authorization source. The
  configuration script assigns a new monotonic policy version to the
  activation watermark, allowlist, publication flag, and cap; the web process
  persists that version in the same Azure Table repository and waits for the
  restricted health endpoint to prove convergence.
- The worker strongly-consistently point-reads this durable policy after
  claiming and again immediately before the ACS transport call. It stores the
  authorizing policy version atomically with the `sending` transition. A queued
  record that was eligible when created is atomically cancelled without an ACS
  call if the durable policy has changed or revoked it. A stale process cannot
  replace a newer policy version.
- Browser polling, reloads, App Service restarts, concurrent workers, and queue
  redelivery cannot create a second ACS submission for a completed event.
- A known ACS 429 or explicit unsuccessful 5xx result receives a bounded,
  exponentially delayed retry. A transport timeout or otherwise unknowable
  external outcome becomes `ambiguous` and is not automatically retried.
- The hard UTC-day limit is 100 ACS submission attempts. Alert 101 remains in
  the application, becomes `cap_blocked`, and creates a redacted operational
  alert without calling ACS.
- Delivery reports are stored before application. Early or partially applied
  reports remain pending and are repaired after correlation appears. An
  out-of-order failure cannot downgrade a recorded delivered message.
- Operational notification, dispatch, delivery, and alert records are deleted
  after 90 days by the Function timer.

## Deployment sequence

1. Run the complete backend/frontend tests and compile the Bicep template.
2. Obtain separate approval to register the required resource providers and
   create Azure resources.
3. Run the manual `Provision ALTER notification infrastructure` GitHub workflow
   with its exact confirmation phrase. It provisions with `SMS_ENABLED=false`
   and `SMS_PUBLISH_ENABLED=false`.
   The configuration script uses `NOTIFICATION_MANAGED_IDENTITY_CLIENT_ID` and
   never overwrites an existing application-wide `AZURE_CLIENT_ID`.
4. Enter the demo recipient through a secure Key Vault command using a secure
   prompt. Do not paste the number into a command line, file, issue, or chat.
5. Confirm Microsoft/ComReg approval for the `ALTER` sender owned by an eligible
   entity. Merely creating ACS does not register or authorise the sender.
6. Validate managed-identity access and resolve any dead-letter/health errors.
   The Function identity uses Azure Service Bus Data Owner scoped only to the
   notification namespace because the Functions scale controller requires the
   role's management-read permission for accurate target-based scaling.
7. Run `scripts/configure-notification-app.ps1` with one current
   `-SmsActivatedAtUtc`, exactly one `-SmsDemoCaseUidAllowlist`,
   `-SmsDailyLimit 1`, and `-SmsPublishEnabled true`. The script writes and
   reads back the same activation, allowlist, cap, publication state, and
   monotonic policy version on the web app and Function. It then waits for the
   restricted health endpoint to prove that the durable Azure Table policy has
   converged. It keeps `SMS_ENABLED=false` on both and keeps
   `AzureWebJobs.sms_dispatch.Disabled=true`. Never use `*` for the first test.
8. Confirm the script completed successfully. Do not manually enable one app
   while leaving the other on a different publication configuration.
9. Run `python scripts/notification_pre_enable_report.py --require-canary` in a
   secured authenticated environment. It must report `safe_to_enable=true` and
   zero eligible notifications, zero eligible schedules, and zero non-canary
   work. Do not continue on a non-zero exit status.
10. Obtain explicit approval for one chargeable handset test. Then update the
    Function in one App Settings operation so `SMS_PUBLISH_ENABLED=true`,
    `SMS_ENABLED=true`, and `AzureWebJobs.sms_dispatch.Disabled=false`; read all
    three values back before creating the alert. The web app remains
    `SMS_PUBLISH_ENABLED=true` and `SMS_ENABLED=false`.
11. Create exactly one new allowlisted alert after the activation watermark.
   Confirm the in-app notification, one ACS message ID, masked recipient, and
   delivered/failed Event Grid record. Delivery does not prove a person read the
   message; acknowledgement stays in ALTER.
12. Disable submission immediately after the test by setting Function
    `SMS_ENABLED=false` and `AzureWebJobs.sms_dispatch.Disabled=true` and read
    both values back. Then rerun `scripts/configure-notification-app.ps1` with
    `-SmsPublishEnabled false`; it creates a newer durable policy version,
    verifies both applications, and proves Azure Table convergence. Do not
    disable publication with an isolated App Settings edit because an already
    running invocation authorizes against the durable policy. Raise the daily
    cap and use the explicit `*` allowlist only after the canary evidence is
    accepted.

## Rollback

Rollback is configuration first. Set Function `SMS_ENABLED=false` and
`AzureWebJobs.sms_dispatch.Disabled=true`, then promote a newer durable policy
with `SMS_PUBLISH_ENABLED=false` on both applications. The authoritative
deployment workflow generates that monotonic rollback version and verifies all
settings before a manually selected ref is deployed; the currently running web
process persists the kill switch before a pre-SMS build can replace it. This
stops new SMS work without removing in-app notifications or deleting audit
evidence. Do not purge queues or tables during incident response. A pre-SMS ref
is allowed to omit notification source files; their absence remains fatal during
a normal current-version deploy.

## Monitoring and incident handling

- `/notifications/system/health` is restricted to the security-status
  permission and exposes no number, SMS body, case ID, or ACS secret.
- Application Insights receives allowlisted state/result logs only.
- A Service Bus metric alert fires when either queue has dead-lettered messages.
  The personal demo initially has no external Action Group; the alert is visible
  in Azure Monitor and must be assigned to an organisational Action Group before
  any production promotion.
- Investigate `ambiguous`, `cap_blocked`, `failed_permanent`, and DLQ records.
  Never manually resend an ambiguous event until the ACS portal/carrier result
  proves it was not accepted.
- After the demonstration, inspect both notification DLQs without opening raw
  delivery bodies. With explicit approval, remove them using:
  `python scripts/cleanup_notification_dlq.py --namespace <namespace>.servicebus.windows.net --confirm PURGE-ALTER-NOTIFICATION-DLQ`.
  This is destructive and permanently completes the selected DLQ messages.

## Future staff routing

The durable record already snapshots `target_role` and optional
`target_user_id`. Production routing must replace only the demonstration
recipient resolver with an approved staff-directory adapter. That promotion is
blocked until there is an authoritative staff identity/contact source, explicit
case assignment, staff consent/opt-out handling, verified E.164 numbers, and a
defined charge-nurse fallback. The demo number must never become the production
fallback.
