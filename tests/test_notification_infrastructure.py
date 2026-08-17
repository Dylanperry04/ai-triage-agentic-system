"""Static release guards for notification Azure infrastructure and deployment."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_infrastructure_is_secure_by_default_and_preserves_hard_limits():
    template = _read("infra/notifications/main.bicep")
    assert "param existingWebAppName string = 'ai-triage-agentic-system'" in template
    assert "param smsPublishEnabled bool = false" in template
    assert "param smsEnabled bool = false" in template
    assert "@maxValue(100)\nparam smsDailyLimit int = 100" in template
    assert "param notificationRetentionDays int = 90" in template
    assert template.count("disableLocalAuth: true") >= 2
    assert template.count("allowSharedKeyAccess: false") >= 2
    assert "requiresDuplicateDetection: true" in template
    assert "duplicateDetectionHistoryTimeWindow: 'P7D'" in template
    assert "SMS_RECIPIENT_MODE: 'demo_allowlist'" in template
    assert "SMS_ACTIVATED_AT_UTC: smsActivatedAtUtc" in template
    assert "SMS_DEMO_CASE_UID_ALLOWLIST: smsDemoCaseUidAllowlist" in template
    assert "SMS_ROLLOUT_POLICY_VERSION: smsRolloutPolicyVersion" in template
    assert "SecretName=alter-demo-sms-recipient" in template
    assert "Microsoft.Communication.SMSDeliveryReportReceived" in template
    assert "defaultMessageTimeToLive: 'P1D'" in template
    assert "090c5cfd-751d-490a-894a-3ce6f1109419" in template
    assert "resource functionBusDataOwnerRole" in template
    assert "resource functionBusReceiverRole" not in template
    assert "resource functionBusSenderRole" not in template


def test_notification_identity_does_not_overwrite_existing_app_identity_setting():
    script = _read("scripts/configure-notification-app.ps1")
    assert "$ResourceGroup = 'Ai-triaging'" in script
    assert "$WebAppName = 'ai-triage-agentic-system'" in script
    assert "NOTIFICATION_MANAGED_IDENTITY_CLIENT_ID=$clientId" in script
    assert '"AZURE_CLIENT_ID=$clientId"' not in script
    assert "$functionAppName = $outputs.functionAppName.value" in script
    assert "az functionapp config appsettings set" in script
    assert '"SMS_PUBLISH_ENABLED=$SmsPublishEnabled"' in script
    assert '"SMS_ROLLOUT_POLICY_VERSION=$SmsRolloutPolicyVersion"' in script
    assert '"AzureWebJobs.sms_dispatch.Disabled=true"' in script
    assert "Notification staging settings did not read back" in script
    for value in (
        "$webDailyLimit",
        "$functionDailyLimit",
        "$webActivatedAt",
        "$functionActivatedAt",
        "$webAllowlist",
        "$functionAllowlist",
        "$webPolicyVersion",
        "$functionPolicyVersion",
    ):
        assert value in script
    assert "Durable rollout policy" in script


def test_resource_creation_is_manual_confirmed_and_sms_remains_disabled():
    workflow = _read(".github/workflows/notifications-infrastructure.yml")
    assert "workflow_dispatch:" in workflow
    assert "CREATE-NOTIFICATION-RESOURCES" in workflow
    assert "RESOURCE_GROUP: Ai-triaging" in workflow
    assert "AZURE_WEBAPP_NAME: ai-triage-agentic-system" in workflow
    assert "Verify authoritative Azure deployment target" in workflow
    assert "smsPublishEnabled=false smsEnabled=false" in workflow
    assert "SMS publication and chargeable submission remain disabled" in workflow
    assert "az provider register" not in workflow


def test_single_authoritative_app_service_workflow_targets_college_app():
    workflow_dir = ROOT / ".github" / "workflows"
    assert not (workflow_dir / "main_triage.yml").exists()
    assert not (workflow_dir / "main_ai-triage-agentic-system.yml").exists()
    deploy = _read(".github/workflows/deploy-azure.yml")
    infrastructure = _read(".github/workflows/notifications-infrastructure.yml")
    assert "AZURE_WEBAPP_NAME: ai-triage-agentic-system" in deploy
    assert "AZURE_RESOURCE_GROUP: Ai-triaging" in deploy
    assert "Triage_System" not in deploy
    assert "Triage_System" not in infrastructure
    assert "Verify authoritative Azure deployment target" in deploy
    correct_credentials = (
        "AZUREAPPSERVICE_CLIENTID_BD0090C04EFB406F8635DC9131E5EAC7",
        "AZUREAPPSERVICE_TENANTID_C617D75FF5BE4DC89A52C293A09788D4",
        "AZUREAPPSERVICE_SUBSCRIPTIONID_AF58CC7D0B264843975E5FF090DEE3C6",
    )
    for secret_name in correct_credentials:
        assert secret_name in deploy
        assert secret_name in infrastructure
    assert "python -m pytest tests/ -q" in deploy
    assert "pnpm test" in deploy
    assert "az bicep build --file infra/notifications/main.bicep" in deploy
    assert deploy.index("Deploy matching notification Function code") < deploy.index(
        "Deploy to Azure Web App"
    )
    assert "ALTER_BUILD_ID=${DEPLOY_SHA}" in deploy


def test_deployment_workflow_has_fail_closed_rollback_and_missing_worker_paths():
    deploy = _read(".github/workflows/deploy-azure.yml")
    assert "NOTIFICATION_SOURCE_PRESENT=$notification_source" in deploy
    assert "Current-version deployment is missing required notification source files" in deploy
    assert "Disable and verify SMS before rollback" in deploy
    assert "SMS_PUBLISH_ENABLED=false SMS_ENABLED=false" in deploy
    assert "AzureWebJobs.sms_dispatch.Disabled=true" in deploy
    assert "Notification Function is absent; web SMS publication and submission were explicitly forced off and verified" in deploy
    assert "SMS_ROLLOUT_POLICY_VERSION=${rollback_policy_version}" in deploy
    assert "SMS_ROLLOUT_POLICY_VERSION=${disabled_policy_version}" in deploy
    assert "env.NOTIFICATION_SOURCE_PRESENT == 'true'" in deploy


def test_deployment_workflow_uses_bounded_runtime_package_and_live_smoke_checks():
    deploy = _read(".github/workflows/deploy-azure.yml")
    assert "RUNTIME_REQUIREMENTS=$runtime_requirements" in deploy
    assert '-r "deployment/${RUNTIME_REQUIREMENTS}"' in deploy
    assert "Enforce deployment package size budget" in deploy
    assert "max_zip_bytes=900000000" in deploy
    assert "WEBSITE_RUN_FROM_PACKAGE" in deploy
    assert "Verify all notification Functions are registered" in deploy
    for function_name in (
        "sms_dispatch",
        "sms_delivery_report",
        "notification_outbox_reconciler",
        "notification_retention_cleanup",
    ):
        assert function_name in deploy
    assert "Post-deployment web and notification smoke test" in deploy
    assert "Verify cleaned deployment application" in deploy
    assert deploy.count("python -S - <<'PY'") == 2
    assert deploy.index("Clean deployment package") < deploy.index(
        "Verify cleaned deployment application"
    ) < deploy.index("Create deployment ZIP")
    assert 'python scripts/azure_smoke_test.py "${args[@]}"' in deploy
    assert "--check-notifications" in deploy
    assert "--require-notification-worker" in deploy
    assert 'get("/health")' not in deploy
    assert 'get("/notifications", protected=True)' not in deploy


def test_canary_runbook_and_gate_require_zero_eligible_backlog():
    operations = _read("docs/ACS_SMS_OPERATIONS.md")
    report = _read("scripts/notification_pre_enable_report.py")
    assert "zero eligible notifications, zero eligible schedules" in operations
    assert 'report["canary_queue_empty"] = canary_queue_empty' in report
    assert "canary_configuration and canary_queue_empty" in report


def test_demo_recipient_is_not_defaulted_in_source_configuration():
    env_example = _read(".env.example")
    assert "DEMO_SMS_RECIPIENT=" not in env_example


def test_local_proxy_and_privacy_cleanup_paths_are_present():
    vite = _read("frontend-react/vite.config.js")
    assert '"/notifications"' in vite
    cleanup = _read("scripts/cleanup_notification_dlq.py")
    assert "ServiceBusSubQueue.DEAD_LETTER" in cleanup
    assert "receiver.complete_message(message)" in cleanup
    assert "message.get_body" not in cleanup


def test_function_does_not_settle_disabled_or_unproven_dispatch_outcomes():
    function_app = _read("functions/notification_worker/function_app.py")
    assert '"sms_disabled"' not in function_app
    assert '"state_conflict"' not in function_app
    assert '"deferred"' in function_app
