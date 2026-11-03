"""New request UX and conversion permissions must agree with authoritative actions."""
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import fetch_one
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, supporting_photo  # noqa: F401
from test_return_request_options import sign_in
from test_replacement_first_policy import request, data, convert
from test_replacement_workflow import approve_receive
from test_sales_case_cards import render


def bridge_client(page, client, origin):
    def bridge(route):
        req = route.request
        url = urlsplit(req.url)
        result = client.request(req.method, url.path + ("?" + url.query if url.query else ""),
            content=req.post_data_buffer, headers={k: v for k, v in req.headers.items()
            if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
        route.fulfill(status=result.status_code, body=result.content, headers={k: v for k, v in result.headers.items()
            if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})
    page.route(origin + "/**", bridge)


@pytest.mark.postgres
def test_new_request_form_has_default_replacement_and_no_refund_or_product_picker(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        result = client.get(f"/portal/reseller/history?view=returns&order={d['order']}")
    assert result.status_code == 200
    form = re.search(r'<form\b[^>]*class="[^"]*return-request-form[^>]*>.*?</form>', result.text, re.S)
    assert form
    html = form.group()
    assert 'novalidate' in html.split('>', 1)[0]
    assert re.search(r'<input[^>]*type="hidden"[^>]*name="resolution"[^>]*value="replace"', html)
    assert 'value="refund"' not in html and 'name="replacement_product_id"' not in html
    assert "same products and quantities" in html.lower()
    assert 'data-replacement-comparison' in html


def test_new_policy_conversion_uses_shared_confirmation_and_revision_guard():
    html = render(role="sales-supervisor", status="settlement_pending", return_policy_version=2, resolution="replace",
                  sales_authorized=True, verified_at=datetime.now(timezone.utc),
                  can_convert_to_refund=True, refund_conversion_version="reviewed-fingerprint")
    assert 'data-return-intent="convert-to-refund"' in html
    assert 'name="review_version" value="reviewed-fingerprint"' in html
    assert 'name="operation_key"' in html
    assert 'name="customer_message"' in html and 'name="remarks"' in html
    assert 'data-return-confirm' in html
    customer = render(role="reseller", return_policy_version=2, can_convert_to_refund=False)
    assert 'data-return-intent="convert-to-refund"' not in customer


def test_converted_refund_does_not_offer_verification_of_unreceived_topup():
    html = render(role='sales-supervisor', resolution='refund', return_policy_version=2,
        status='settlement_pending', sales_authorized=True, has_payment_proof=True,
        refund_converted_at=datetime.now(timezone.utc), can_convert_to_refund=False,
        replacement_payment_details=dict(transaction_number='NOTRECEIVED', payment_amount='100', revision=1))
    assert 'actions/verify-payment' not in html
    assert 'Confirm funds received' not in html


@pytest.mark.postgres
def test_order_picker_has_inline_validation_and_native_keyboard_selection(lifecycle, monkeypatch):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    d = lifecycle
    sign_in(monkeypatch, d['reseller'], 'reseller')
    with TestClient(main.app, base_url='http://picker.test') as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 375, 'height': 800})
        bridge_client(page, client, 'http://picker.test')
        page.goto('http://picker.test/portal/reseller/history?view=returns&request=1', wait_until='networkidle')
        picker = page.locator('[data-return-request-dialog] select[name="order"]')
        page.get_by_role('button', name='Continue', exact=True).click()
        expect(picker).to_have_attribute('aria-invalid', 'true')
        expect(picker).to_be_focused()
        expect(page.get_by_role('button', name='Continue', exact=True)).not_to_have_class(re.compile(r'\bis-loading\b'))
        picker.press('Alt+ArrowDown')
        picker.press('ArrowDown')
        picker.press('Enter')
        expect(picker).to_have_value(str(d['order']))
        page.get_by_role('button', name='Continue', exact=True).click()
        expect(page.locator('.return-request-form')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert page.evaluate("getComputedStyle(document.documentElement).scrollbarColor !== 'auto'")
        page.emulate_media(forced_colors='active')
        assert page.evaluate("getComputedStyle(document.documentElement).scrollbarColor === 'auto'")
        browser.close()


@pytest.mark.postgres
def test_new_policy_cannot_be_bypassed_by_posting_refund_or_legacy_version(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app) as client:
        result = client.post("/portal/reseller/returns/actions/submit", data={
            "order_id": d["order"], "allocation_id": d["allocation"], "quantity": "2",
            "resolution": "refund", "return_policy_version": "1", "remarks": "Try legacy request bypass",
        }, files={"evidence": ("proof.png", supporting_photo()["content"], "image/png")})
    assert result.status_code in (200, 303)
    assert fetch_one("SELECT count(*) n FROM product_returns")["n"] == 0


@pytest.mark.postgres
@pytest.mark.parametrize("width", [375, 1440])
def test_default_request_browser_comparison_failure_retry_and_submit(lifecycle, monkeypatch, width):  # noqa: F811
    from playwright.sync_api import sync_playwright, expect
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    output = Path("outputs/replacement-policy")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://policy.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        fail = {"preview": True, "submit": True}

        def bridge(route):
            req = route.request
            url = urlsplit(req.url)
            if "replacement-preview" in url.path and fail["preview"]:
                fail["preview"] = False
                route.fulfill(status=503, body='{"error":"Comparison unavailable. Try again."}', content_type="application/json")
                return
            if req.method == 'POST' and url.path.endswith('/actions/submit') and fail['submit']:
                fail['submit'] = False
                route.fulfill(status=503, json={'error': 'Request unavailable. Retry with your current details.'})
                return
            result = client.request(req.method, url.path + ("?" + url.query if url.query else ""),
                                    content=req.post_data_buffer, headers={k: v for k, v in req.headers.items()
                                    if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=result.status_code, body=result.content, headers={k: v for k, v in result.headers.items()
                          if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://policy.test/**", bridge)
        page.goto(f"http://policy.test/portal/reseller/history?view=returns&order={d['order']}", wait_until="networkidle")
        form = page.locator(".return-request-form")
        expect(form.locator('[name="resolution"][value="refund"]')).to_have_count(0)
        form.get_by_label("Return quantity for Test Product batch TEST-1").fill("2")
        form.get_by_label("Reason and remarks").fill("Damaged packs need the same product replacement")
        form.get_by_label("Supporting photos").set_input_files({"name": "proof.png", "mimeType": "image/png", "buffer": supporting_photo()["content"]})
        form.get_by_role("button", name="Request replacement", exact=True).click()
        expect(form.get_by_role("button", name="Try comparison again", exact=True)).to_be_visible()
        form.get_by_role("button", name="Try comparison again", exact=True).click()
        expect(form.locator('[name="comparison_token"]')).not_to_have_value("")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        page.screenshot(path=str(output / f"request-{width}.png"), full_page=True)
        key = form.locator('[name="operation_key"]').input_value()
        form.get_by_role("button", name="Request replacement", exact=True).click()
        expect(form.locator('[data-return-request-feedback]')).to_contain_text('Request unavailable')
        expect(form.locator('[name="remarks"]')).to_have_value('Damaged packs need the same product replacement')
        assert form.locator('[name="evidence"]').evaluate('el => el.files.length') == 1
        assert form.locator('[name="operation_key"]').input_value() == key
        assert fetch_one('SELECT count(*) n FROM product_returns')['n'] == 0
        form.get_by_role("button", name="Request replacement", exact=True).click()
        page.wait_for_url(re.compile(r'[?&]case='), wait_until='networkidle')
        case = fetch_one("SELECT return_id,return_policy_version,status FROM product_returns")
        assert case["return_policy_version"] == 2 and case["status"] == "submitted"
        assert not errors
        browser.close()


@pytest.mark.postgres
def test_converted_case_has_no_delivery_or_extra_payment_queue(lifecycle):  # noqa: F811
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    convert(d, case)
    result = data(d, case)
    from app.return_timeline import workflow_steps
    steps = workflow_steps(result)
    assert not any(step["key"] in {"delivery", "prepare", "payment", "release"} for step in steps)
    assert result["refund"]["amount"] == 200


@pytest.mark.postgres
@pytest.mark.parametrize("role", ["sales", "inventory", "reseller"])
def test_conversion_controls_and_http_action_are_processor_only(lifecycle, monkeypatch, role):  # noqa: F811
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    version = returns.refund_conversion_version(data(d, case))
    prefix = "reseller" if role == "reseller" else "team-leader"
    sign_in(monkeypatch, d[role], prefix, role)
    from uuid import uuid4
    with TestClient(main.app) as client:
        response = client.get(f"/portal/{prefix}/returns?case={case}")
        assert response.status_code == 200
        assert 'data-refund-conversion' not in response.text
        response = client.post(f"/portal/{prefix}/returns/actions/convert-to-refund", headers={"Accept": "application/json"}, data={
            "return_id": case, "remarks": "Unrelated role cannot make this decision",
            "customer_message": "Unauthorized conversion attempt", "operation_key": str(uuid4()), "review_version": version,
        })
        assert response.status_code in {403, 422}
    assert data(d, case)["resolution"] == "replace" and not data(d, case)["refund"]


@pytest.mark.postgres
@pytest.mark.parametrize("role,width", [("supervisor", 375), ("supervisor", 1440), ("owner", 375), ("owner", 1440)])
def test_processor_conversion_confirmation_retry_refunds_return_credit(lifecycle, monkeypatch, role, width):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    d = lifecycle
    case = request(d)
    returns.review_product_return(case, d['supervisor'], True, 'Approve the reseller submitted replacement')
    returns.inspect_product_return(case, d["inventory"], "complete", "All approved quantities and proof match", str(uuid4()))
    prefix = "sales-supervisor" if role == "supervisor" else "owner"
    sign_in(monkeypatch, d[role], prefix, prefix)
    output = Path("outputs/replacement-policy")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url="http://convert.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        fail = {"once": True}

        def bridge(route):
            req = route.request
            url = urlsplit(req.url)
            if req.method == "POST" and url.path.endswith('/convert-to-refund') and fail["once"]:
                fail["once"] = False
                route.fulfill(status=503, json={"error": "Conversion is temporarily unavailable. Try again."})
                return
            result = client.request(req.method, url.path + ("?" + url.query if url.query else ""), content=req.post_data_buffer,
                headers={k: v for k, v in req.headers.items() if k not in {"host", "content-length", "cookie"}}, follow_redirects=True)
            route.fulfill(status=result.status_code, body=result.content, headers={k: v for k, v in result.headers.items()
                if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}})

        page.route("http://convert.test/**", bridge)
        page.goto(f"http://convert.test/portal/{prefix}/returns?case={case}", wait_until="networkidle")
        if role == "supervisor":
            page.get_by_role("tab", name="Settlement", exact=True).click()
        form = page.locator('[data-refund-conversion]')
        expect(form).to_be_visible()
        form.get_by_role("button", name="Convert to refund", exact=True).click()
        expect(form.locator('[name="remarks"]')).to_be_focused()
        expect(form.locator('[name="remarks"]')).to_have_attribute('aria-invalid', 'true')
        form.locator('[name="remarks"]').fill("Supplier cannot supply any acceptable replacement")
        form.locator('[name="customer_message"]').fill("We cannot supply a suitable replacement. We will refund your approved return credit.")
        expect(form).to_contain_text("PHP 200.00")
        key = form.locator('[name="operation_key"]').input_value()
        form.get_by_role("button", name="Convert to refund", exact=True).click()
        confirmation = page.get_by_role("dialog", name="Convert replacement to refund?", exact=True)
        expect(confirmation).to_be_visible()
        expect(confirmation.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
        expect(confirmation).to_contain_text("PHP 200.00")
        assert confirmation.evaluate("el => getComputedStyle(el).backgroundColor !== 'rgba(0, 0, 0, 0)'")
        assert confirmation.evaluate("el => {const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;}")
        confirmation.get_by_role("button", name="Cancel", exact=True).click()
        assert data(d, case)["resolution"] == "replace"
        form.get_by_role("button", name="Convert to refund", exact=True).click()
        confirmation.get_by_role("button", name="Confirm refund conversion", exact=True).click()
        expect(form.locator('[data-conversion-feedback]')).to_contain_text("temporarily unavailable")
        assert form.locator('[name="operation_key"]').input_value() == key
        expect(form.locator('[name="remarks"]')).to_have_value("Supplier cannot supply any acceptable replacement")
        form.get_by_role("button", name="Convert to refund", exact=True).click()
        expect(confirmation).to_be_visible()
        page.screenshot(path=str(output / f"conversion-{role}-{width}.png"))
        confirmation.get_by_role("button", name="Confirm refund conversion", exact=True).click()
        page.wait_for_load_state("networkidle")
        expect(page.locator('[data-refund-conversion]')).to_have_count(0)
        expect(page.locator('form[action$="/verify-payment"]')).to_have_count(0)
        result = data(d, case)
        assert result["resolution"] == "refund" and result["refund"]["amount"] == 200
        assert not errors, errors
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        browser.close()


@pytest.mark.postgres
@pytest.mark.parametrize("width", [320, 768])
def test_request_discard_preserves_draft_until_confirmed_and_clears_dirty_state(lifecycle, monkeypatch, width):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    with TestClient(main.app, base_url="http://discard.test") as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 740}, reduced_motion="reduce")
        bridge_client(page, client, "http://discard.test")
        page.goto(f"http://discard.test/portal/reseller/history?view=returns&order={d['order']}", wait_until="networkidle")
        form = page.locator('.return-request-form')
        form.locator('[name="quantity"]').fill('2')
        form.locator('[name="remarks"]').fill('Preserve my draft until I choose to discard it')
        form.get_by_role('button', name='Request replacement', exact=True).click()
        expect(form.locator('[name="comparison_token"]')).not_to_have_value('')
        page.keyboard.press('Escape')
        dialog = page.get_by_role('dialog', name='Discard unsaved changes?', exact=True)
        expect(dialog.get_by_role('button', name='Keep editing')).to_be_focused()
        assert dialog.evaluate('el => {const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;}')
        page.screenshot(path=f'outputs/replacement-policy/discard-{width}.png')
        dialog.get_by_role('button', name='Keep editing').click()
        expect(form.locator('[name="remarks"]')).to_have_value('Preserve my draft until I choose to discard it')
        page.keyboard.press('Escape')
        dialog.get_by_role('button', name='Discard changes').click()
        expect(page.locator('[data-return-request-dialog]')).not_to_be_visible()
        expect(form.locator('[name="quantity"]')).to_have_value('0')
        expect(form.locator('[name="comparison_token"]')).to_have_value('')
        page.evaluate('() => {window.cleanDiscard=null; PortalForms.confirmDiscard(document).then(value => window.cleanDiscard=value)}')
        page.wait_for_function('window.cleanDiscard === true', timeout=2000)
        assert fetch_one('SELECT count(*) n FROM product_returns')["n"] == 0
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        browser.close()


@pytest.mark.postgres
@pytest.mark.parametrize("role,width", [("supervisor", 375), ("owner", 1440)])
def test_equal_value_replacement_has_no_payment_action(lifecycle, monkeypatch, role, width):  # noqa: F811
    from playwright.sync_api import expect, sync_playwright
    d = lifecycle
    case = request(d)
    approve_receive(d, case)
    prefix = 'sales-supervisor' if role == 'supervisor' else 'owner'
    sign_in(monkeypatch, d[role], prefix, prefix)
    with TestClient(main.app, base_url='http://stale.test') as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion='reduce')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        bridge_client(page, client, 'http://stale.test')
        page.goto(f'http://stale.test/portal/{prefix}/returns?case={case}', wait_until='networkidle')
        expect(page.locator('form[action$="/verify-payment"]')).to_have_count(0)
        assert not errors, errors
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        browser.close()
    from test_product_lifecycle import refund_proof
    with pytest.raises(ValueError):
        returns.record_return_payment(case, d['reseller'], [refund_proof()], {
            'transaction_number': 'UNEXPECTED', 'payment_amount': '100',
            'payment_date': '2026-11-03', 'payment_time': '10:00',
        })
