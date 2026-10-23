from __future__ import annotations

from copy import deepcopy

import psycopg2
import pytest

from app.database import fetch_all, fetch_one, get_transaction_cursor
from app.security import verify_password
from tools.provision_demo_accounts import provision
from test_inventory_postgres import strict_inventory_database  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture
def account_batch(strict_inventory_database):  # noqa: F811
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash) VALUES('owner','Existing Owner','owner@existing.test','unchanged');")
    leaders = [{"name": f"Demo Leader {i}", "email": f"leader{i}@meattrack.test", "password": f"Unique-Leader-{i}!Password"} for i in range(3)]
    resellers = [{"name": f"Demo Reseller {i}", "email": f"reseller{i}@meattrack.test", "password": f"Unique-Reseller-{i}!Password", "business_name": f"Demo Business {i}", "contact_number": "DEMO-ONLY", "address": "Demo, Batangas", "leader_email": leaders[i // 2]["email"]} for i in range(5)]
    return {"batch_key": "test-account-batch", "sales_leaders": leaders, "resellers": resellers}


def test_account_batch_links_resellers_and_reruns_without_resetting_passwords(account_batch):
    first = provision(account_batch, "verified-before-change.dump")
    assert len(first["accounts"]) == 8
    assert first["reused_existing_batch"] is False
    rows = fetch_all("SELECT a.email,a.password_hash,a.activation_status,a.is_active,l.email leader_email FROM accounts a LEFT JOIN resellers r USING(reseller_id) LEFT JOIN accounts l ON l.account_id=r.team_leader_account_id WHERE a.email <> 'owner@existing.test';")
    expected = {entry["email"]: entry for entry in account_batch["sales_leaders"] + account_batch["resellers"]}
    for row in rows:
        assert row["is_active"] and row["activation_status"] == "active"
        assert verify_password(expected[row["email"]]["password"], row["password_hash"])
        assert row["leader_email"] == expected[row["email"]].get("leader_email")
    changed_manifest = deepcopy(account_batch)
    changed_manifest["sales_leaders"][0]["password"] = "Different!Password123"
    second = provision(changed_manifest, "verified-before-change.dump")
    assert second["reused_existing_batch"] is True
    assert second["accounts"] == first["accounts"]
    assert fetch_one("SELECT count(*) total FROM accounts;")["total"] == 9
    assert fetch_one("SELECT count(*) total FROM activity_logs WHERE action='provisioned_test-account-batch';")["total"] == 8
    assert fetch_one("SELECT password_hash FROM accounts WHERE email='leader0@meattrack.test';")["password_hash"] == next(row["password_hash"] for row in rows if row["email"] == "leader0@meattrack.test")
    assert fetch_one("SELECT password_hash FROM accounts WHERE email='owner@existing.test';")["password_hash"] == "unchanged"


def test_late_creation_failure_rolls_back_leaders_resellers_and_audit_records(account_batch):
    with get_transaction_cursor() as cur:
        cur.execute("""CREATE FUNCTION fail_demo_account() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.email='reseller4@meattrack.test' THEN RAISE EXCEPTION 'injected provisioning failure'; END IF;
            RETURN NEW; END; $$;
            CREATE TRIGGER fail_demo_account BEFORE INSERT ON accounts FOR EACH ROW EXECUTE FUNCTION fail_demo_account();""")
    with pytest.raises(psycopg2.errors.RaiseException, match="injected provisioning failure"):
        provision(account_batch, "verified-before-change.dump")
    assert fetch_one("SELECT count(*) total FROM accounts;")["total"] == 1
    assert fetch_one("SELECT count(*) total FROM resellers;")["total"] == 0
    assert fetch_one("SELECT count(*) total FROM activity_logs;")["total"] == 0


def test_existing_email_collision_leaves_existing_accounts_untouched(account_batch):
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash,team_leader_role) VALUES('team_leader','Existing Sales','leader0@meattrack.test','original-password','sales');")
    with pytest.raises(ValueError, match="email already exists"):
        provision(account_batch, "verified-before-change.dump")
    assert fetch_one("SELECT count(*) total FROM accounts;")["total"] == 2
    assert fetch_one("SELECT password_hash FROM accounts WHERE email='leader0@meattrack.test';")["password_hash"] == "original-password"
    assert fetch_one("SELECT count(*) total FROM resellers;")["total"] == 0
