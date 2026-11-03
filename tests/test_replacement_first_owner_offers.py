"""Owner fallback must use the same guarded offer confirmation and recovery."""
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main
from app.database import fetch_one
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, substitute  # noqa: F401
from test_replacement_first_policy import request
from test_replacement_first_portal import bridge_client
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('role,width', [('owner', 375), ('owner', 1440), ('supervisor', 375), ('supervisor', 1440)])
def test_owner_alternative_confirmation_and_lower_value_error_preserve_form(lifecycle, monkeypatch, role, width):  # noqa: F811
    d = lifecycle
    case = request(d)
    cheaper, higher = substitute(150), substitute(300)
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
        form.locator('.sales-return-substitutions > summary').click()
        if role == 'owner':
            form.locator('[name="product_id"]').first.select_option(str(cheaper))
            assert form.locator('[name="customer_message"]').evaluate('el => el.required')
            page.locator('[data-return-dialog] [data-return-close]').click()
            discard = page.get_by_role('dialog', name='Discard unsaved changes?', exact=True)
            discard.get_by_role('button', name='Discard changes', exact=True).click()
            expect(page.locator('[data-return-dialog]')).not_to_be_visible()
            page.locator(f'[data-return-open="{case}"]').click()
            expect(form.locator('[name="product_id"]').first).to_have_value('')
            assert not form.locator('[name="customer_message"]').evaluate('el => el.required')
        form.locator('[name="product_id"]').first.select_option(str(cheaper))
        form.locator('[name="replacement_quantity"]').first.fill('1')
        form.locator('[name="customer_message"]').fill('The original product is unavailable. Please review this alternative.')
        form.locator('[name="remarks"]').fill('Supplier cannot provide the original product')
        page.get_by_role('button', name='Approve return request', exact=True).click()
        dialog = page.get_by_role('dialog', name='Approve return request?', exact=True)
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role('button', name='Cancel', exact=True)).to_be_focused()
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        assert fetch_one('SELECT status FROM product_returns WHERE return_id=%s', (case,))['status'] == 'submitted'
        page.get_by_role('button', name='Approve return request', exact=True).click()
        dialog.get_by_role('button', name='Confirm approval', exact=True).click()
        expect(form.locator('[data-return-form-feedback]')).to_contain_text('equal or exceed')
        expect(form.locator('[name="product_id"]').first).to_have_value(str(cheaper))
        expect(form.locator('[name="remarks"]')).to_have_value('Supplier cannot provide the original product')
        assert fetch_one('SELECT quote_revision FROM product_returns WHERE return_id=%s', (case,))['quote_revision'] == 1
        form.locator('[name="product_id"]').first.select_option(str(higher))
        page.get_by_role('button', name='Approve return request', exact=True).click()
        expect(dialog).to_be_visible()
        assert dialog.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}')
        Path('outputs/replacement-policy').mkdir(parents=True, exist_ok=True)
        page.screenshot(path=f'outputs/replacement-policy/{role}-offer-{width}.png')
        dialog.get_by_role('button', name='Confirm approval', exact=True).click()
        page.wait_for_url(re.compile(r'[?&]message='), wait_until='networkidle')
        state = fetch_one('SELECT status,quote_revision,accepted_revision,replacement_total FROM product_returns WHERE return_id=%s', (case,))
        assert state['status'] == 'awaiting_receipt' and state['quote_revision'] == 2
        assert state['accepted_revision'] != 2 and state['replacement_total'] == 300
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors, errors
        browser.close()
