import argparse
import json
from pathlib import Path
from typing import Any

from backend import config
from backend.db import Database
from wizard.validator import ManifestError


def _template_actions(template: str) -> dict[str, Any]:
    if template == "custom":
        return {"activate": {}}
    return {
        action: {"duration_s": {"max": config.ACTION_DURATION_CEILINGS[action]}}
        if action in config.ACTION_DURATION_CEILINGS else {}
        for action in sorted(config.DEVICE_ACTIONS[template])
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register a TwinLock device from a template.")
    parser.add_argument("--db", default=config.MOCK_DB_PATH)
    parser.add_argument("--user", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--manifest", type=Path, help="JSON file with policy overrides")
    parser.add_argument("template", choices=(*config.DEVICE_ACTIONS, "custom"))
    args = parser.parse_args(argv)

    try:
        overrides = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else {}
        if not isinstance(overrides, dict) or {"id", "owner", "type"} & overrides.keys():
            raise ManifestError("invalid_template_overrides")
        manifest = {
            "id": args.device_id, "owner": args.user, "type": args.template,
            "actions": _template_actions(args.template), **overrides,
        }
        store = Database(args.db)
        try:
            store.register_device(manifest)  # Uses the shared manifest validator.
        finally:
            store.close()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"registered {args.device_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
