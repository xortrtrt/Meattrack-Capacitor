from pathlib import Path

from playwright.sync_api import expect, sync_playwright


PORTAL_SCRIPT = Path("app/static/js/portal.js")


def test_portal_filters_refresh_results_without_replacing_controls() -> None:
    initial_html = """
    <!doctype html><html><body>
      <section data-portal-content>
        <strong data-async-filter-sync="test-count">2</strong>
        <nav class="movement-categories" data-async-filter-target="[data-async-filter-results]" data-async-filter-sync="test-tabs">
          <a href="?type=beef">Beef</a>
        </nav>
        <form class="filter-bar" method="get" data-async-filter-target="[data-async-filter-results]">
          <input type="hidden" name="type" value="" data-async-filter-sync="test-type">
          <input id="query" name="q" type="search">
          <select name="status"><option value="">All</option><option value="active">Active</option></select>
        </form>
        <div data-async-filter-results data-async-filter-target="[data-async-filter-results]">
          <span data-result>initial</span>
          <div class="pagination"><a href="?page=2">Next</a></div>
        </div>
      </section>
    </body></html>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("http://meattrack.test/portal", lambda route: route.fulfill(body=initial_html, content_type="text/html"))
        page.goto("http://meattrack.test/portal", wait_until="networkidle")
        page.evaluate(
            """
            window.lucide = { createIcons() {} };
            window.fetch = async (input) => {
              const url = new URL(String(input), window.location.href);
              const q = url.searchParams.get("q") || "";
              const status = url.searchParams.get("status") || "";
              const type = url.searchParams.get("type") || "";
              const page = url.searchParams.get("page") || "1";
              const summary = `q=${q};status=${status};type=${type};page=${page}`;
              const html = `<!doctype html><html><head><title>Filtered</title></head><body>
                <strong data-async-filter-sync="test-count">1</strong>
                <nav class="movement-categories" data-async-filter-target="[data-async-filter-results]" data-async-filter-sync="test-tabs">
                  <a class="${type === "beef" ? "is-active" : ""}" href="?type=beef">Beef</a>
                </nav>
                <input type="hidden" name="type" value="${type}" data-async-filter-sync="test-type">
                <div data-async-filter-results data-async-filter-target="[data-async-filter-results]">
                  <span data-result>${summary}</span>
                  <div class="pagination"><a href="?type=${type}&page=2">Next</a></div>
                </div>
              </body></html>`;
              return new Response(html, { status: 200, headers: { "Content-Type": "text/html" } });
            };
            """
        )
        page.add_script_tag(content=PORTAL_SCRIPT.read_text(encoding="utf-8"))

        query = page.locator("#query")
        query.fill("kit")
        expect(page.locator("[data-result]")).to_contain_text("q=kit", timeout=2_000)
        expect(query).to_be_focused()
        expect(query).to_have_value("kit")

        page.locator('select[name="status"]').select_option("active")
        expect(page.locator("[data-result]")).to_contain_text("status=active")
        expect(query).to_have_value("kit")

        page.locator(".movement-categories a").click()
        expect(page.locator("[data-result]")).to_contain_text("type=beef")
        expect(page.locator('input[name="type"]')).to_have_value("beef")
        expect(page.locator("[data-async-filter-sync='test-count']")).to_have_text("1")

        page.locator(".pagination a").click()
        expect(page.locator("[data-result]")).to_contain_text("page=2")
        assert page.url.endswith("type=beef&page=2")

        browser.close()


def test_portal_sign_out_requires_confirmation_and_restores_focus() -> None:
    html = """
    <!doctype html><html><body>
      <form action="/logout" method="post" data-logout-form>
        <button type="submit" data-sign-out aria-controls="logout-confirmation-dialog">Sign out</button>
      </form>
      <div data-logout-modal hidden>
        <button type="button" data-logout-close>Backdrop</button>
        <div id="logout-confirmation-dialog" role="dialog" tabindex="-1">
          <button type="button" data-logout-close>Stay signed in</button>
          <button type="button" data-logout-confirm><span>Sign out</span></button>
        </div>
      </div>
    </body></html>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "http://meattrack.test/portal",
            lambda route: route.fulfill(body=html, content_type="text/html"),
        )
        page.route(
            "http://meattrack.test/logout",
            lambda route: route.fulfill(body="Signed out", content_type="text/html"),
        )
        page.goto("http://meattrack.test/portal", wait_until="networkidle")
        page.evaluate("window.lucide = { createIcons() {} }")
        page.add_script_tag(content=PORTAL_SCRIPT.read_text(encoding="utf-8"))

        trigger = page.locator("[data-sign-out]")
        modal = page.locator("[data-logout-modal]")
        confirm = page.locator("[data-logout-confirm]")

        trigger.click()
        expect(modal).to_be_visible()
        expect(confirm).to_be_focused()

        page.get_by_role("button", name="Stay signed in").click()
        expect(modal).to_be_hidden()
        expect(trigger).to_be_focused()

        trigger.click()
        page.keyboard.press("Escape")
        expect(modal).to_be_hidden()
        expect(trigger).to_be_focused()

        trigger.click()
        with page.expect_request("http://meattrack.test/logout") as request_info:
            confirm.click()
        assert request_info.value.method == "POST"

        browser.close()
