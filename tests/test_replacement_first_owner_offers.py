"""Owner and Supervisor review must approve the submitted selection as-is."""
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401
from test_replacement_first_policy import request
from test_replacement_first_portal import bridge_client
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('role,width', [('owner', 375), ('owner', 1440), ('supervisor', 375), ('supervisor', 1440)])
def test_owner_and_supervisor_approve_fixed_replacement_without_notes(lifecycle, monkeypatch, role, width):  # noqa: F811
    d = lifecycle
    case = request(d)
    prefix = 'owner' if role == 'owner' else 'sales-supervisor'
    sign_in(monkeypatch, d[role], prefix, prefix)
    with TestClient(main.app, base_url='http://owner-offer.test') as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        bridge_client(page, client, 'http://owner-offer.test')
        page.goto(f'http://owner-offer.test/portal/{prefix}/returns?case={case}', wait_until='networkidle')
        if role == 'supervisor':
            page.get_by_role('tab', name='Review Decision', exact=True).click()
        form = page.locator('form[action$="/approve"]')
        expect(form.locator('.sales-return-substitutions')).to_have_count(0)
        expect(form.locator('[name="product_id"]')).to_have_count(0)
        expect(form.locator('[name="customer_message"]')).to_have_count(0)
        expect(form.get_by_label('Internal review notes', exact=True)).to_have_count(0)
        page.get_by_role('button', name='Approve return request', exact=True).click()
        dialog = page.get_by_role('dialog', name='Approve return request?', exact=True)
        expect(dialog).to_be_visible()
        expect(dialog.locator('[data-confirm-description]')).to_contain_text('submitted replacement as requested')
        expect(dialog.locator('[data-confirm-description]')).to_contain_text('received and verified')
        assert dialog.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}')
        Path('outputs/replacement-policy').mkdir(parents=True, exist_ok=True)
        dialog.get_by_role('button', name='Confirm approval', exact=True).click()
        page.wait_for_url(re.compile(r'[?&]message='), wait_until='networkidle')
        state = fetch_one('SELECT status,quote_revision,accepted_revision,replacement_total FROM product_returns WHERE return_id=%s', (case,))
        assert state['status'] == 'awaiting_receipt' and state['quote_revision'] == 1
        assert state['accepted_revision'] == 1 and state['replacement_total'] == 200
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors, errors
        browser.close()
