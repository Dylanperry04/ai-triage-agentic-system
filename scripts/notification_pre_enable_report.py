"""Read-only fail-closed gate for the single-handset SMS canary."""
from __future__ import annotations

import argparse
import json
import sys

from app.notifications.config import NotificationSettings
from app.notifications.repository import get_notification_repository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report SMS-eligible durable work without reading message bodies or recipients."
    )
    parser.add_argument(
        "--require-canary",
        action="store_true",
        help=(
            "Also require publish-only mode, one allowlisted case, a daily cap of 1, "
            "and no existing eligible notification or schedule work."
        ),
    )
    args = parser.parse_args()
    settings = NotificationSettings.from_env()
    repository = get_notification_repository(settings)
    allowed = set(settings.sms_demo_case_uid_allowlist)
    report = repository.pre_enable_report(allowed_case_uids=allowed)
    canary_configuration = (
        settings.sms_publish_enabled
        and not settings.sms_enabled
        and settings.daily_limit == 1
        and len(allowed) == 1
        and "*" not in allowed
        and bool(settings.sms_activated_at_utc)
    )
    canary_queue_empty = (
        int(report.get("eligible_notifications") or 0) == 0
        and int(report.get("eligible_schedules") or 0) == 0
    )
    report["canary_configuration_valid"] = canary_configuration
    report["canary_queue_empty"] = canary_queue_empty
    report["safe_to_enable"] = bool(report["safe_to_enable"]) and (
        canary_configuration and canary_queue_empty if args.require_canary else True
    )
    # The report deliberately contains no case IDs, message content, recipient,
    # sender credential, ACS endpoint, or queue payload.
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["safe_to_enable"] else 2


if __name__ == "__main__":
    sys.exit(main())
