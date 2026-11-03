from urllib.parse import parse_qs, urlsplit

import pytest

from app import repositories
from app.database import fetch_one, get_transaction_cursor
from app.portals import owner
from app.security import hash_password
from test_inventory_postgres import strict_inventory_database  # noqa: F401


@pytest.mark.postgres
def test_reactivated_account_can_authenticate_with_original_password(request):
    request.getfixturevalue("strict_inventory_database")
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO accounts(account_type,name,email,password_hash) VALUES('owner','Owner','owner@status.test','hash') RETURNING account_id")
        actor_id = cur.fetchone()['account_id']
        cur.execute("INSERT INTO resellers(business_name,contact_person,email,contact_number) VALUES('Store','Person','reseller@status.test','+639171234567') RETURNING reseller_id")
        reseller_id = cur.fetchone()['reseller_id']
        cur.execute("INSERT INTO accounts(account_type,reseller_id,name,email,password_hash) VALUES('reseller',%s,'Person','reseller@status.test',%s) RETURNING account_id", (reseller_id, hash_password('Original-Password1!')))
        account_id = cur.fetchone()['account_id']
    deactivated = repositories.set_account_active(account_id, False, actor_id, 'Temporary access suspension')
    assert repositories.authenticate_account('reseller@status.test', 'Original-Password1!') is None
    queued = fetch_one('SELECT payload FROM notification_outbox WHERE outbox_id=%s', (deactivated['status_email_outbox_id'],))
    assert queued['payload']['reason'] == 'Temporary access suspension'
    reactivated = repositories.set_account_active(account_id, True, actor_id, 'Restore account access')
    notice = fetch_one('SELECT event_type, payload FROM notification_outbox WHERE outbox_id=%s', (reactivated['status_email_outbox_id'],))
    assert notice['event_type'] == 'account_reactivated'
    assert notice['payload']['reason'] == 'Restore account access'
    assert repositories.authenticate_account('reseller@status.test', 'Original-Password1!')['account_id'] == account_id
    assert fetch_one('SELECT reseller_status FROM resellers WHERE reseller_id=%s', (reseller_id,))['reseller_status'] == 'active'


@pytest.mark.parametrize('sent', [True, False])
@pytest.mark.parametrize('active', ['true', 'false'])
def test_status_change_reports_email_result(monkeypatch, sent, active):
    monkeypatch.setattr(owner.shared, 'require_portal_session', lambda *args: None)
    monkeypatch.setattr(owner.shared, 'session_account_id', lambda *args: 1)
    monkeypatch.setattr(owner, 'require_capability', lambda *args: None)
    monkeypatch.setattr(owner, 'require_owner_step_up', lambda *args: None)
    monkeypatch.setattr(owner.data, 'set_account_active', lambda *args: {'status_email_outbox_id': 3, 'email': 'person@status.test', 'name': 'Person'})
    calls = []
    monkeypatch.setattr(owner, 'send_account_deactivation_notice', lambda **kwargs: (sent, 'Delivery unavailable'))
    monkeypatch.setattr(owner, 'send_account_reactivation_notice', lambda **kwargs: (sent, 'Delivery unavailable'))
    monkeypatch.setattr(owner.data, 'finish_account_status_email', lambda *args: calls.append(args))
    response = owner.owner_account_status(None, 2, active, 'Temporary suspension')
    result = parse_qs(urlsplit(response.headers['location']).query)
    assert ('message' in result) == sent
    assert ('error' in result) != sent
    assert calls == [(3, sent, 'Delivery unavailable')]


def test_reactivation_email_has_restored_access_and_escaped_reason(monkeypatch):
    from app import emailer, worker
    calls = []
    monkeypatch.setattr(emailer, '_send_email', lambda **kwargs: calls.append(kwargs) or (True, 'Accepted'))
    worker._deliver({'event_type': 'account_reactivated', 'payload': {
        'to_email': 'person@status.test', 'name': 'Person', 'reason': 'Review <complete>',
    }})
    assert 'existing password' in calls[0]['body']
    assert 'Review <complete>' in calls[0]['body']
    assert 'Review &lt;complete&gt;' in calls[0]['html_body']
    assert calls[0]['to_email'] == 'person@status.test'
