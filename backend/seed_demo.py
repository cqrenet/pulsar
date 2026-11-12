"""Insert synthetic audit events and one alert, for a local sandbox without a Microsoft tenant.

    docker compose exec backend python seed_demo.py --count 200

Events are marked `demo-*` and include tenant-controlled-looking strings (markdown links, formula prefixes, HTML)
so output encoding can be checked in the UI, CSV export and alert cards. Never run this against real data.
"""

import argparse
from datetime import UTC, datetime, timedelta

from database import alerts_collection, events_collection
from models.event_model import normalize_event

SERVICES = ["UserManagement", "GroupManagement", "RoleManagement", "Device", "Exchange", "SharePoint", "Intune"]
OPERATIONS = ["Add user", "Update user", "Add member to role", "Delete user", "Set-Mailbox", "Add application"]
TRICKY_NAMES = [
    "Alice Example",
    "[Verify your account](https://evil.example/login)",
    '=HYPERLINK("https://evil.example","click")',
    "<img src=x onerror=alert(1)>",
    "Bob O'Example",
]


def build(i: int) -> dict:
    when = datetime.now(UTC) - timedelta(minutes=17 * i)
    name = TRICKY_NAMES[i % len(TRICKY_NAMES)]
    return normalize_event(
        {
            "id": f"demo-{i:05d}",
            "correlationId": f"demo-corr-{i // 3}",
            "activityDateTime": when.isoformat().replace("+00:00", "Z"),
            "category": SERVICES[i % len(SERVICES)],
            "activityDisplayName": OPERATIONS[i % len(OPERATIONS)],
            "result": "failure" if i % 7 == 0 else "success",
            "initiatedBy": {"user": {"displayName": name, "userPrincipalName": f"user{i % 9}@demo.example"}},
            "targetResources": [{"displayName": f"target-{i % 11} {name}", "type": "User"}],
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=100)
    args = parser.parse_args()
    for i in range(args.count):
        doc = build(i)
        events_collection.update_one({"dedupe_key": doc["dedupe_key"]}, {"$set": doc}, upsert=True)
    alerts_collection.update_one(
        {"rule_name": "Demo alert"},
        {
            "$setOnInsert": {
                "timestamp": datetime.now(UTC).isoformat(),
                "rule_id": "demo",
                "rule_name": "Demo alert",
                "severity": "high",
                "service": "RoleManagement",
                "operation": "Add member to role",
                "actor": TRICKY_NAMES[1],
                "message": "Synthetic alert for the sandbox",
                "status": "open",
            }
        },
        upsert=True,
    )
    print(f"Seeded {args.count} demo events and 1 demo alert.")


if __name__ == "__main__":
    main()
