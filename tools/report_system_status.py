from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.superuser import report_system_status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record a non-secret worker, backup, or retention health report for the owner portal."
    )
    parser.add_argument("component", choices=("backup", "worker", "retention_job"))
    parser.add_argument("status", choices=("ok", "warning", "error", "unknown"))
    parser.add_argument("--detail", default="")
    parser.add_argument("--metadata", default="{}", help="JSON object with safe, non-secret report metadata.")
    args = parser.parse_args()
    metadata = json.loads(args.metadata)
    if not isinstance(metadata, dict):
        raise SystemExit("--metadata must be a JSON object")
    report_system_status(args.component, args.status, args.detail, metadata)


if __name__ == "__main__":
    main()
