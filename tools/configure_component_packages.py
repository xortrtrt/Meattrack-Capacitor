"""Apply the three explicitly approved package definitions to a migrated database."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.repositories.inventory.packages import configure_reference_packages

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor-id", type=int, required=True)
    parser.add_argument(
        "--archive-legacy", action="store_true", help="Record corrections clearing the old standalone package balances"
    )
    args = parser.parse_args()
    configure_reference_packages(args.actor_id, archive_legacy=args.archive_legacy)
    print("Configured Trial (16 packs), Reseller (70 packs), and Distributor (400 packs).")
