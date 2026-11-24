import argparse
from dataclasses import asdict
import json

from backend import config
from backend.approver_api import ApproverAPI
from backend.db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review TwinLock leases for a user.")
    parser.add_argument("--db", default=config.MOCK_DB_PATH)
    parser.add_argument("--user", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for command in ("approve", "deny"):
        commands.add_parser(command).add_argument("lease_id")
    args = parser.parse_args(argv)

    store = Database(args.db)
    try:
        approver = ApproverAPI(store)
        if args.command == "list":
            entries = []
            for pending in approver.list_pending(args.user):
                entry = asdict(pending)
                entry["params"] = json.loads(pending.params)
                entries.append(entry)
            print(json.dumps(entries, indent=2))
            return 0
        transition = approver.approve if args.command == "approve" else approver.deny
        if transition(args.lease_id, user_id=args.user):
            print("approved" if args.command == "approve" else "denied")
            return 0
        print("refused: no matching pending lease")
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
