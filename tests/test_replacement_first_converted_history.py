"""Converted cases retain offer history without inviting replacement actions."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, substitute  # noqa: F401
from test_replacement_first_policy import request, offer, convert
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('role', ['reseller', 'supervisor', 'owner'])
def test_conversion_before_acceptance_shows_historical_offer_without_next_actions(lifecycle, monkeypatch, role):  # noqa: F811
    d = lifecycle
    case = request(d)
    offer(d, case, substitute(300))
    convert(d, case)
    prefix = {'reseller': 'reseller', 'supervisor': 'sales-supervisor', 'owner': 'owner'}[role]
    sign_in(monkeypatch, d[role], prefix, prefix)
    path = f'/portal/{prefix}/history?view=returns&case={case}' if role == 'reseller' else f'/portal/{prefix}/returns?case={case}'
    with TestClient(main.app) as client:
        result = client.get(path)
        assert result.status_code == 200
        assert 'actions/accept' not in result.text
        assert 'Replacement offer history' in result.text
        assert 'You will receive' not in result.text
        assert 'Awaiting reseller acceptance' not in result.text
        assert 'Payment proof under review' not in result.text
        assert 'Replacement stopped' in result.text
        if role == 'reseller':
            inbox = client.get(f'/portal/reseller/inbox?replacement={case}')
            assert inbox.status_code == 200
            context = client.get(f'/api/portal/reseller/inbox/contacts/{case}').json()['context']
            assert context['refund_converted_at'] and context['refund_due'] == 200
            assert context['resolution'] == 'refund' and context['additional_due'] == 0
    view = returns.get_product_return(case, d['reseller'])
    assert view['additional_due'] == 0 and view['unused_credit'] == 0
    assert view['quote_revision'] == 2 and view['accepted_revision'] != 2


def test_converted_inventory_view_keeps_receipt_task_and_hides_money(lifecycle, monkeypatch):  # noqa: F811
    from test_inventory_sales_separation import assert_no_money
    d = lifecycle
    case = request(d)
    offer(d, case, substitute(300))
    convert(d, case)
    view = returns.get_product_return(case, d['inventory'])
    assert_no_money(view)
    assert view['inventory_can_confirm_receipt']
    assert not view['inventory_can_reserve'] and not view['inventory_can_release']
    sign_in(monkeypatch, d['inventory'], 'team-leader', 'inventory')
    with TestClient(main.app) as client:
        result = client.get(f'/portal/team-leader/returns?case={case}')
        assert result.status_code == 200
        assert 'PHP' not in result.text and '₱' not in result.text
        assert 'Confirm complete receipt' in result.text
        assert 'actions/release' not in result.text


@pytest.mark.parametrize('width', [375, 1440])
def test_converted_contact_history_displays_refund_and_verified_topup(lifecycle, monkeypatch, width):  # noqa: F811
    from pathlib import Path
    from playwright.sync_api import expect, sync_playwright
    from test_replacement_first_portal import bridge_client
    from test_replacement_workflow import approve_receive, pay
    d = lifecycle
    case = request(d)
    offer(d, case, substitute(300))
    returns.accept_replacement_quote(case, d['reseller'], 2)
    approve_receive(d, case)
    pay(d, case, 100)
    convert(d, case)
    sign_in(monkeypatch, d['reseller'], 'reseller', 'reseller')
    with TestClient(main.app, base_url='http://converted-inbox.test') as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        bridge_client(page, client, 'http://converted-inbox.test')
        page.goto(f'http://converted-inbox.test/portal/reseller/inbox?replacement={case}', wait_until='networkidle')
        summary = page.locator('[data-contact-summary]')
        expect(summary).to_contain_text('Refund amount')
        if not summary.evaluate('el => el.open'):
            summary.locator('summary').click()
        context = page.locator('[data-contact-context]')
        expect(context).to_contain_text('Replacement stopped')
        expect(context).to_contain_text('Verified extra payment to return')
        expect(context).to_contain_text('300.00')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        Path('outputs/replacement-policy').mkdir(parents=True, exist_ok=True)
        page.screenshot(path=f'outputs/replacement-policy/converted-inbox-{width}.png')
        assert not errors, errors
        browser.close()
