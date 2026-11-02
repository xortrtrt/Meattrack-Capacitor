"""Catalog presentation and real ordering behavior in a disposable database schema."""

from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, repositories, web_sessions
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from test_inventory_postgres import _seed_fulfillment, strict_inventory_database  # noqa: F401

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


@pytest.fixture
def catalog(strict_inventory_database, monkeypatch):  # noqa: F811
    _seed_fulfillment(strict_inventory_database, batch_quantities=[], order_quantities=[])
    account = fetch_one("SELECT account_id FROM accounts WHERE email='account@test.local'")["account_id"]
    names = [
        "Tocino Ala Eh", "Bacon Smoked", "Beef Longganisa", "Cheesy Overload Sausage",
        "Chicken Rebusado", "Beef Tapa Ala Eh", "Pork Tapa", "Chicken Tocino", "Deli Beef",
    ]
    products = []
    with get_transaction_cursor() as cur:
        # All catalog data belongs to the isolated schema, never the development catalog.
        cur.execute("DELETE FROM inventory_items WHERE name='Test Product'")
        for index in range(27):
            name = names[index] if index < len(names) else f"Catalog selection {index:02d}"
            category = ("Pork", "Chicken", "Beef")[index % 3]
            price = Decimal("70.50") + Decimal(index) * Decimal("3.25")
            available = 0 if index == 0 else 1 if index == 6 else index + 10
            description = f"Catalog fixture {index}: product description for browsing."
            if index == 2:
                description = " ".join([description] * 20)
            cur.execute(
                """INSERT INTO inventory_items(item_type,category,name,description,unit,base_price)
                   VALUES('finished_product',%s,%s,%s,'pack',%s) RETURNING item_id""",
                (category, name, description, price),
            )
            product_id = cur.fetchone()["item_id"]
            if available:
                cur.execute(
                    """INSERT INTO inventory_batches(item_id,batch_code,source_type,quantity_received,
                           quantity_available,unit,received_date,expiry_date,quality_status)
                       VALUES(%s,%s,'direct_received',%s,%s,'pack',CURRENT_DATE,CURRENT_DATE+30,'approved')""",
                    (product_id, f"CATALOG-{index}", available, available),
                )
            # Expired physical packs must not appear in current availability.
            cur.execute(
                """INSERT INTO inventory_batches(item_id,batch_code,source_type,quantity_received,
                       quantity_available,unit,received_date,expiry_date,quality_status)
                   VALUES(%s,%s,'direct_received',99,99,'pack',CURRENT_DATE-10,CURRENT_DATE-1,'approved')""",
                (product_id, f"CATALOG-EXPIRED-{index}"),
            )
            products.append(dict(product_id=product_id, name=name, category=category,
                                 price=price, available=available, description=description))
    session = dict(account_id=account, role_key="reseller",
                   portal_expires_at=int(business_now().timestamp()) + 600)
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    return dict(products=products, account=account, session=session)


@pytest.fixture
def catalog_browser(catalog):
    with TestClient(main.app, base_url="http://catalog.test") as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            request = route.request
            parts = urlsplit(request.url)
            response = client.request(
                request.method, parts.path + ("?" + parts.query if parts.query else ""),
                content=request.post_data_buffer,
                headers={key: value for key, value in request.headers.items()
                         if key not in {"host", "content-length", "cookie"}},
                follow_redirects=True,
            )
            route.fulfill(status=response.status_code, body=response.content,
                          headers={key: value for key, value in response.headers.items()
                                   if key not in {"content-length", "content-encoding", "transfer-encoding"}})

        page.route("http://catalog.test/**", bridge)
        page.goto("http://catalog.test/portal/reseller/order")
        page.wait_for_load_state("networkidle")
        yield page, client
        browser.close()
        assert not errors


def card_ids(page):
    return page.locator("[data-product-card]").evaluate_all(
        "cards => cards.map(card => Number(card.dataset.productId))"
    )


def test_catalog_search_categories_sort_and_pagination(catalog, catalog_browser):
    page, _ = catalog_browser
    products = catalog["products"]
    expect(page.get_by_role("heading", name="Products", exact=True).last).to_be_visible()
    expect(page.locator(".reseller-catalog-count")).to_have_text("Showing 1–8 of 27 products")
    expect(page.locator(".reseller-product-card small")).to_have_count(0)

    for category in ("Chicken", "Pork", "Beef"):
        page.get_by_role("navigation", name="Product categories").get_by_role("link", name=category, exact=True).click()
        expect(page.locator('input[name="type"]')).to_have_value(category)
        expect(page.locator(".reseller-category-tabs [aria-current]")).to_have_text(category)
        expected = [product["product_id"] for product in products if product["category"] == category]
        expect(page.locator(".reseller-catalog-count")).to_have_text("Showing 1–8 of 9 products")
        assert card_ids(page) == expected[:8]

    # Search matches the existing name/description query, including beyond page one.
    page.get_by_role("searchbox", name="Search products").fill("catalog")
    page.wait_for_url("**q=catalog*")
    expect(page.locator(".reseller-catalog-count")).to_have_text("Showing 1–8 of 9 products")
    page.get_by_role("link", name="Next", exact=True).click()
    expect(page.locator(".reseller-catalog-count")).to_have_text("Showing 9–9 of 9 products")
    assert card_ids(page) == expected[8:]
    query = parse_qs(urlsplit(page.url).query)
    assert query["type"] == ["Beef"] and query["q"] == ["catalog"] and query["page"] == ["2"]

    # A category change resets pagination and keeps the current search.
    page.get_by_role("navigation", name="Product categories").get_by_role("link", name="All", exact=True).click()
    expect(page.locator(".reseller-catalog-count")).to_have_text("Showing 1–8 of 27 products")
    assert "q=catalog" in page.url and "page=" not in page.url
    for sort, key, reverse in (
        ("name_asc", "name", False), ("price_asc", "price", False),
        ("price_desc", "price", True), ("stock_desc", "available", True),
    ):
        page.get_by_label("Sort products").select_option(sort)
        page.wait_for_url(f"**sort={sort}*")
        expected_sorted = sorted(products, key=lambda product: product[key], reverse=reverse)
        assert card_ids(page) == [product["product_id"] for product in expected_sorted[:8]]
        page.get_by_role("link", name="Next", exact=True).click()
        expect(page.locator(".reseller-catalog-count")).to_have_text("Showing 9–16 of 27 products")
        assert card_ids(page) == [product["product_id"] for product in expected_sorted[8:16]]
        assert parse_qs(urlsplit(page.url).query)["sort"] == [sort]

    page.get_by_role("navigation", name="Product pages").get_by_role("link", name="Page 4", exact=True).click()
    expect(page.locator('.reseller-product-pagination [aria-current="page"]')).to_have_text("4")
    assert card_ids(page) == [product["product_id"] for product in expected_sorted[24:]]
    expect(page.locator('.reseller-product-pagination .reseller-page-disabled')).to_have_text("Next")
    page.get_by_role("link", name="Reset filters").click()
    expect(page.get_by_role("searchbox", name="Search products")).to_have_value("")
    expect(page.get_by_label("Sort products")).to_have_value("stock_desc")
    expect(page.locator(".reseller-catalog-active-filters")).to_have_count(0)

    page.get_by_role("searchbox", name="Search products").fill("no-such-catalog-product")
    expect(page.get_by_role("heading", name="No matching products")).to_be_visible()
    expect(page.locator(".reseller-catalog-count")).to_have_text("0 products")
    page.get_by_role("link", name="Clear filters").click()
    expect(page.locator(".reseller-catalog-count")).to_have_text("Showing 1–8 of 27 products")
    page.get_by_role("searchbox", name="Search products").fill("Chicken Rebusado")
    expect(page.locator("[data-product-card]")).to_have_count(1)
    expect(page.locator("[data-product-card]")).to_have_attribute("data-product-name", "Chicken Rebusado")


def test_catalog_responsive_details_images_and_cart(catalog, catalog_browser):
    page, _ = catalog_browser
    output = Path("outputs/reseller-catalog")
    output.mkdir(parents=True, exist_ok=True)
    first, second = catalog["products"][:2]

    for width, columns in ((1920, 4), (1440, 3), (768, 2), (560, 2), (390, 2), (320, 1)):
        page.set_viewport_size({"width": width, "height": 1000 if width > 860 else 844})
        page.locator(".portal-sidebar").evaluate(
            "element => Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => {})))"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("[data-product-grid]").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        ) == columns
        cards = page.locator("[data-product-card]")
        sizes = cards.evaluate_all("cards => cards.map(card => card.getBoundingClientRect().height)")
        assert max(sizes) - min(sizes) < 2
        for image in page.locator(".reseller-product-image img").all():
            image.scroll_into_view_if_needed()
            expect(image).to_have_js_property("complete", True)
            assert image.evaluate("image => image.naturalWidth > 0")
        page.evaluate("scrollTo(0,0)")
        if width in (1440, 390):
            page.evaluate("document.activeElement?.blur()")
            page.mouse.move(0, 0)
            page.screenshot(path=str(output / f"catalog-{width}.png"), full_page=True)
            page.screenshot(path=str(output / f"catalog-viewport-{width}.png"))

        cards.nth(1).get_by_role("button").click()
        dialog = page.get_by_role("dialog", name=second["name"])
        expect(dialog).to_be_visible()
        expect(dialog.locator("[data-product-modal-price]")).to_have_text(f"PHP {second['price']:,.2f}/pack")
        expect(dialog.locator("[data-product-modal-stock]")).to_have_text(f"{second['available']} packs")
        expect(dialog.locator("[data-product-modal-description]")).to_have_text(second["description"])
        expect(dialog.locator("[data-product-modal-category]")).to_have_text(second["category"])
        expect(dialog.locator("[data-product-modal-quantity]")).to_have_attribute("max", str(second["available"]))
        expect(dialog.locator("[data-product-modal-image]")).to_have_js_property("complete", True)
        assert dialog.locator("[data-product-modal-image]").evaluate("image => image.naturalWidth > 0")
        assert dialog.locator("[data-product-modal-image]").evaluate("""image => {
            const frame = image.parentElement.getBoundingClientRect();
            const bounds = image.getBoundingClientRect();
            return bounds.top >= frame.top && bounds.bottom <= frame.bottom + 1
                && bounds.left >= frame.left && bounds.right <= frame.right + 1;
        }""")
        assert dialog.evaluate("element => element.scrollWidth <= element.clientWidth")
        expect(dialog.get_by_role("button", name=f"Add {second['name']} to cart")).to_be_enabled()
        expect(dialog.get_by_role("button", name="Decrease quantity")).to_be_disabled()
        quantity = dialog.locator("[data-product-modal-quantity]")
        quantity.fill(str(second["available"]))
        expect(dialog.get_by_role("button", name="Increase quantity")).to_be_disabled()
        quantity.fill("1")
        if width in (1440, 390):
            page.screenshot(path=str(output / f"details-{width}.png"))
        dialog.get_by_role("button", name=f"Add {second['name']} to cart").focus()
        page.keyboard.press("Tab")
        expect(dialog.get_by_role("button", name="Close product details")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(dialog.get_by_role("button", name=f"Add {second['name']} to cart")).to_be_focused()
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(cards.nth(1).get_by_role("button")).to_be_focused()

    # Long descriptions scroll inside the dialog while its close control remains reachable.
    page.set_viewport_size({"width": 390, "height": 844})
    cards.nth(2).get_by_role("button").click()
    dialog = page.get_by_role("dialog", name=catalog["products"][2]["name"])
    expect(dialog.locator("[data-product-modal-description]")).to_have_text(catalog["products"][2]["description"])
    dialog.evaluate("element => element.scrollTop = element.scrollHeight")
    expect(dialog.get_by_role("button", name="Close product details")).to_be_in_viewport()
    dialog.get_by_role("button", name="Close product details").click()
    expect(dialog).not_to_be_visible()

    cards.first.get_by_role("button").click()
    dialog = page.get_by_role("dialog", name=first["name"])
    expect(cards.first.locator(".reseller-catalog-stock")).to_contain_text("Out of stock")
    expect(dialog.locator("[data-product-modal-quantity]")).to_be_disabled()
    expect(dialog.get_by_role("button", name=f"Add {first['name']} to cart")).to_be_disabled()
    expect(dialog.get_by_role("button", name="Close product details")).to_be_focused()
    expect(dialog.get_by_role("button", name="Increase quantity")).to_be_disabled()
    expect(dialog.get_by_role("button", name="Decrease quantity")).to_be_disabled()
    page.keyboard.press("Escape")

    # The existing modal form posts to the existing cart route, preserving validation.
    cards.nth(1).get_by_role("button").click()
    dialog = page.get_by_role("dialog", name=second["name"])
    quantity = dialog.locator("[data-product-modal-quantity]")
    dialog.get_by_role("button", name="Increase quantity").click()
    expect(quantity).to_have_value("2")
    quantity.fill(str(second["available"] + 1))
    dialog.get_by_role("button", name=f"Add {second['name']} to cart").click()
    assert not quantity.evaluate("element => element.checkValidity()")
    assert repositories.list_reseller_cart_items(catalog["account"]) == []
    quantity.fill("2")
    dialog.get_by_role("button", name=f"Add {second['name']} to cart").click()
    expect(page.get_by_text("Product added to cart.", exact=True)).to_be_visible()
    items = repositories.list_reseller_cart_items(catalog["account"])
    assert len(items) == 1 and items[0]["product_id"] == second["product_id"]
    assert items[0]["cart_quantity"] == 2
    assert Decimal(str(items[0]["line_total"])) == second["price"] * 2
    assert repositories.list_products(q=second["name"])[0]["available"] == second["available"]
    assert fetch_one("SELECT COUNT(*) AS total FROM all_stock_reservations")["total"] == 0


def test_catalog_permissions_and_authoritative_cart_validation(catalog):
    product = catalog["products"][1]
    with TestClient(main.app) as client:
        response = client.post("/portal/reseller/order", data={"product_id": product["product_id"], "quantity": "999"})
        assert response.status_code == 200 and "Only 11 packs" in response.text
        assert repositories.list_reseller_cart_items(catalog["account"]) == []
        catalog["session"].clear()
        assert client.get("/portal/reseller/order", follow_redirects=False).status_code == 303
        assert client.post("/portal/reseller/order", data={"product_id": product["product_id"], "quantity": "1"},
                           follow_redirects=False).status_code == 303
        catalog["session"].update(account_id=catalog["account"], role_key="owner",
                                   portal_expires_at=int(business_now().timestamp()) + 600)
        assert client.get("/portal/reseller/order", follow_redirects=False).status_code == 303
