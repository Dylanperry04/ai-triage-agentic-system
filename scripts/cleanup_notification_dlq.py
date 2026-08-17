"""Explicitly receive and complete ALTER notification DLQ messages.

The ACS delivery DLQ can contain the destination number in Microsoft-generated
event data. This utility never reads or logs message bodies.
"""
from __future__ import annotations

import argparse
import os
import sys


CONFIRMATION = "PURGE-ALTER-NOTIFICATION-DLQ"


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge secured notification DLQs without logging payloads.")
    parser.add_argument("--namespace", required=True, help="Service Bus fully qualified namespace")
    parser.add_argument(
        "--queue", action="append", dest="queues",
        choices=("alter-sms-dispatch", "alter-sms-delivery"),
        help="Queue to clean; repeat for both. Defaults to both notification queues.",
    )
    parser.add_argument(
        "--max-messages", type=int, default=1000,
        help="Safety limit per selected queue; repeat the command if limit_reached=true.",
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error(f"--confirm must equal {CONFIRMATION}")
    if not 1 <= args.max_messages <= 10000:
        parser.error("--max-messages must be between 1 and 10000")

    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    from azure.servicebus import ServiceBusClient, ServiceBusSubQueue

    client_id = os.environ.get("NOTIFICATION_MANAGED_IDENTITY_CLIENT_ID", "").strip()
    credential = (
        ManagedIdentityCredential(client_id=client_id)
        if client_id else DefaultAzureCredential(exclude_interactive_browser_credential=True)
    )
    queues = args.queues or ["alter-sms-dispatch", "alter-sms-delivery"]
    total = 0
    with ServiceBusClient(args.namespace, credential=credential) as client:
        for queue in queues:
            removed = 0
            with client.get_queue_receiver(
                queue_name=queue, sub_queue=ServiceBusSubQueue.DEAD_LETTER,
                max_wait_time=5, prefetch_count=0,
            ) as receiver:
                while removed < args.max_messages:
                    batch = receiver.receive_messages(
                        max_message_count=min(50, args.max_messages - removed),
                        max_wait_time=5,
                    )
                    if not batch:
                        break
                    for message in batch:
                        receiver.complete_message(message)
                        removed += 1
                        total += 1
            print(
                f"queue={queue} removed={removed} "
                f"limit_reached={str(removed == args.max_messages).lower()}"
            )
    print(f"total_removed={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
