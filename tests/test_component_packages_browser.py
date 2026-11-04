"""Product photo form in Product Details."""

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions, repositories
from app.database import fetch_one
from app.business_time import business_now
from test_inventory_postgres import strict_inventory_database  # noqa: F401

from test_component_packages import bundle_catalog  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
@pytest.mark.e2e
def test_component_package_browser(bundle_catalog, monkeypatch):  # noqa: F811
    data, products = bundle_catalog
    session = dict(
        account_id=data["inventory"],
        role_key="team-leader",
        team_leader_role="inventory",
        portal_expires_at=int(business_now().timestamp()) + 600,
    )
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda token, values, *args: session.update(values))
    with TestClient(main.app, base_url="http://product-photo.test") as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            request = route.request
            parts = urlsplit(request.url)
            path = parts.path + ("?" + parts.query if parts.query else "")
            headers = {
                key: value for key, value in request.headers.items() if key not in {"host", "content-length", "cookie"}
            }
            response = client.request(
                request.method, path, content=request.post_data_buffer, headers=headers, follow_redirects=False
            )
            response_headers = {
                key: value
                for key, value in response.headers.items()
                if key not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
            }
            if response.status_code == 303:
                location = response.headers["location"]
                # Follow within TestClient; route interception does not preserve all redirect semantics.
                response = client.get(location)
                response_headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}
                }
            route.fulfill(status=response.status_code, headers=response_headers, body=response.content)

        page.route("http://product-photo.test/**", bridge)
        page.goto("http://product-photo.test/portal/team-leader/finished-products?type=Package")
        expect(page.locator(".inventory-product-card--package")).to_have_count(3)
        expect(page.get_by_text("Individual products", exact=True)).to_have_count(0)
        page.get_by_role("link", name="Trial Package").click()
        expect(page.get_by_text("16 included product packs per package", exact=True)).to_be_visible()
        expect(page.get_by_text("50 can be assembled", exact=True)).to_be_visible()
        expect(page.get_by_text("Remove batch", exact=True)).to_have_count(0)
        session.update(account_id=data["reseller"], role_key="reseller", team_leader_role=None)
        page.goto("http://product-photo.test/portal/reseller/order?type=Package")
        expect(page.locator("[data-product-card]")).to_have_count(3)
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 900})
            page.get_by_role("button", name="View Trial Package", exact=True).click()
            expect(page.locator("[data-product-modal-description]")).to_contain_text("Sliced Ham")
            expect(page.locator("[data-product-modal-description]")).to_contain_text("Bacon")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            bounds = page.get_by_role('dialog').bounding_box()
            assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 901
            page.screenshot(path=f"outputs/component-package-{width}.png", full_page=False)
            page.keyboard.press("Escape")
        page.get_by_role("button", name="View Trial Package", exact=True).click()
        page.locator('[data-product-modal] button[type="submit"]').click()
        page.goto("http://product-photo.test/portal/reseller/cart")
        expect(page.get_by_role("heading", name="Trial Package", exact=True)).to_be_visible()
        expect(page.get_by_text("16 included packs", exact=True)).to_be_visible()
        page.goto(f'http://product-photo.test/portal/reseller/checkout?product_id={products["Trial Package"]}')
        expect(page.get_by_text('16 packs per package',exact=True)).to_be_visible()
        # All mutations stay inside this fixture's disposable schema.
        page.get_by_role('button',name='Place order',exact=True).click()
        expect(page.get_by_label('Ordered packages')).to_contain_text('Trial Package')
        order=fetch_one('SELECT order_id FROM orders WHERE created_by_account_id=%s ORDER BY order_id DESC LIMIT 1',(data['reseller'],))
        page.goto(f'http://product-photo.test/portal/reseller/checkout?placed={order["order_id"]}')
        expect(page.get_by_label('Ordered packages')).to_contain_text('Trial Package')
        page.goto(f'http://product-photo.test/portal/reseller/history?order={order["order_id"]}&q=Trial%20Package')
        group=page.locator('[data-order-package]')
        expect(group).to_have_count(1)
        expect(group).to_contain_text('Trial Package')
        expect(group).to_contain_text('16 included product packs')
        group.locator('summary').click()
        expect(group).to_contain_text('Sliced Ham')
        expect(group).to_contain_text('Bacon')
        from test_inventory_postgres import _payment_submission
        from app.database import get_transaction_cursor
        from decimal import Decimal
        proof,payment=_payment_submission('staff-package-first')
        payment['payment_amount']=Decimal('1399')
        repositories.submit_order_payment(data['reseller'],order['order_id'],proof,payment)
        with get_transaction_cursor() as cur:
            cur.execute('UPDATE resellers SET team_leader_account_id=%s WHERE reseller_id=(SELECT reseller_id FROM accounts WHERE account_id=%s)',(data['supervisor'],data['reseller']))
        repositories.add_reseller_cart_item(data['reseller'],products['Trial Package'],'1')
        second= repositories.checkout_reseller_cart(data['reseller'])
        proof,payment=_payment_submission('staff-package-second')
        payment['payment_amount']=Decimal('1399')
        repositories.submit_order_payment(data['reseller'],second['order_id'],proof,payment)
        for role,actor,role_detail,own,other in (
            ('team-leader',data['sales'],'sales',order,second),
            ('sales-supervisor',data['supervisor'],'sales-supervisor',second,order),
        ):
            session.update(account_id=actor,role_key=role,team_leader_role=role_detail)
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':900})
                page.goto(f'http://product-photo.test/portal/{role}/orders?q=Trial%20Package&order={own["order_id"]}')
                group=page.locator('[data-order-package]')
                expect(group).to_have_count(1)
                expect(group).to_contain_text('Trial Package')
                expect(group).to_contain_text('16 included product packs')
                expect(group).to_contain_text('1,399')
                expect(page.locator(f'#order-{other["order_id"]}')).to_have_count(0)
                group.locator('summary').click()
                expect(group).to_contain_text('Sliced Ham')
                expect(group).to_contain_text('Bacon')
                assert group.locator('.order-package-component > div > strong').first.evaluate("element => getComputedStyle(element).whiteSpace") == 'normal'
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=f'outputs/staff-package-{role}-{width}.png',full_page=True)
        assert not errors, errors
        browser.close()
