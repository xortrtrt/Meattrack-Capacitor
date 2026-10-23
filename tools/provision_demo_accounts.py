"""Provision a private, explicitly requested batch of linked demonstration accounts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import get_transaction_cursor
from app.repositories._shared import _add_log_cursor
from app.security import hash_password, validate_password_policy


def validate_manifest(manifest: dict) -> dict:
    batch_key = str(manifest.get("batch_key", ""))
    if not re.fullmatch(r"[a-z0-9-]{5,60}", batch_key):
        raise ValueError("Use a short, unique batch key containing lowercase letters, numbers, and hyphens.")
    leaders, resellers = manifest.get("sales_leaders", []), manifest.get("resellers", [])
    if len(leaders) != 3 or len(resellers) != 5:
        raise ValueError("This demo batch requires three sales leaders and five resellers.")
    emails = set()
    for entry in leaders + resellers:
        entry["name"] = " ".join(str(entry.get("name", "")).split())
        entry["email"] = str(entry.get("email", "")).strip().lower()
        if not entry["name"] or not re.fullmatch(r"[^\s@]+@meattrack\.test", entry["email"]):
            raise ValueError("Each demo identity needs a name and a reserved @meattrack.test email address.")
        if entry["email"] in emails:
            raise ValueError("Demo email addresses must be distinct.")
        emails.add(entry["email"])
        entry["password"] = validate_password_policy(str(entry.get("password", "")))
    leader_emails = {entry["email"] for entry in leaders}
    for entry in resellers:
        entry["leader_email"] = str(entry.get("leader_email", "")).strip().lower()
        if entry["leader_email"] not in leader_emails:
            raise ValueError("Every reseller must reference one of this batch's sales leaders.")
        for field in ("business_name", "contact_number", "address"):
            entry[field] = " ".join(str(entry.get(field, "")).split())
            if not entry[field]:
                raise ValueError(f"Each reseller needs {field.replace('_', ' ')}.")
    return manifest


def provision(manifest: dict, backup_reference: str) -> dict:
    manifest = validate_manifest(manifest)
    if not backup_reference.strip():
        raise ValueError("Record the verified pre-change database backup before provisioning.")
    marker = f"provisioned_{manifest['batch_key']}"
    leaders, resellers = manifest["sales_leaders"], manifest["resellers"]
    entries = leaders + resellers
    with get_transaction_cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s));", (manifest["batch_key"],))
        cur.execute("SELECT account_id FROM accounts WHERE account_type='owner' AND is_active=true ORDER BY account_id LIMIT 1;")
        owner = cur.fetchone()
        if not owner:
            raise ValueError("An active owner is required for the provisioning audit.")
        cur.execute("SELECT account_id,email,account_type,team_leader_role,name,reseller_id FROM accounts WHERE lower(email)=ANY(%s) FOR UPDATE;", ([entry["email"] for entry in entries],))
        existing = {row["email"].lower(): dict(row) for row in cur.fetchall()}
        if existing:
            if len(existing) != len(entries):
                raise ValueError("An email already exists; no accounts were changed.")
            for entry in entries:
                account = existing[entry["email"]]
                expected_type = "team_leader" if entry in leaders else "reseller"
                cur.execute("SELECT 1 FROM activity_logs WHERE action=%s AND entity_type='accounts' AND entity_id=%s;", (marker, account["account_id"]))
                if not cur.fetchone() or account["account_type"] != expected_type or account["name"] != entry["name"]:
                    raise ValueError("An email belongs to another identity; no accounts were changed.")
                if expected_type == "team_leader" and account["team_leader_role"] != "sales":
                    raise ValueError("The existing leader role changed; no accounts were changed.")
            reused = True
        else:
            reused = False
            for entry in leaders:
                cur.execute("""INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role,is_active,activation_status,activated_at)
                    VALUES('team_leader',%s,%s,%s,'sales',true,'active',now())
                    RETURNING account_id,email,name;""", (entry["name"], entry["email"], hash_password(entry["password"])))
                existing[entry["email"]] = dict(cur.fetchone())
                _add_log_cursor(cur, actor_account_id=owner["account_id"], action=marker, entity_type="accounts", entity_id=existing[entry["email"]]["account_id"])
            for entry in resellers:
                leader_id = existing[entry["leader_email"]]["account_id"]
                cur.execute("""INSERT INTO resellers(business_name,contact_person,email,contact_number,address,reseller_status,team_leader_account_id,approved_by_account_id,approved_at)
                    VALUES(%s,%s,%s,%s,%s,'active',%s,%s,now()) RETURNING reseller_id;""",
                    (entry["business_name"], entry["name"], entry["email"], entry["contact_number"], entry["address"], leader_id, owner["account_id"]))
                reseller_id = cur.fetchone()["reseller_id"]
                cur.execute("""INSERT INTO accounts(account_type,reseller_id,name,email,password_hash,is_active,activation_status,activated_at)
                    VALUES('reseller',%s,%s,%s,%s,true,'active',now()) RETURNING account_id,email,name;""",
                    (reseller_id, entry["name"], entry["email"], hash_password(entry["password"])))
                existing[entry["email"]] = dict(cur.fetchone())
                _add_log_cursor(cur, actor_account_id=owner["account_id"], action=marker, entity_type="accounts", entity_id=existing[entry["email"]]["account_id"])
                _add_log_cursor(cur, actor_account_id=owner["account_id"], action="assigned_reseller_team_leader", entity_type="resellers", entity_id=reseller_id)
        accounts = []
        for entry in entries:
            account = existing[entry["email"]]
            if entry in resellers:
                cur.execute("SELECT r.team_leader_account_id,r.business_name FROM resellers r JOIN accounts a USING(reseller_id) WHERE a.account_id=%s;", (account["account_id"],))
                profile = cur.fetchone()
                if not profile or profile["team_leader_account_id"] != existing[entry["leader_email"]]["account_id"] or profile["business_name"] != entry["business_name"]:
                    raise ValueError("Reseller assignment differs from this batch; no accounts were changed.")
            accounts.append({"account_id": account["account_id"], "name": entry["name"], "email": entry["email"], "role": "sales" if entry in leaders else "reseller", "leader_email": entry.get("leader_email")})
        return {"batch_key": manifest["batch_key"], "backup_reference": backup_reference, "reused_existing_batch": reused, "accounts": accounts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Private JSON file; never commit credentials.")
    parser.add_argument("--backup-reference", required=True)
    parser.add_argument("--report", type=Path, required=True, help="Private output report; contains no passwords.")
    args = parser.parse_args()
    if args.report.exists():
        raise SystemExit("Report already exists; preserve the previous provisioning record.")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    result = provision(json.loads(args.manifest.read_text(encoding="utf-8")), args.backup_reference)
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print("Verified 3 sales leaders, 5 reseller accounts, and all reseller assignments.")


if __name__ == "__main__":
    main()
