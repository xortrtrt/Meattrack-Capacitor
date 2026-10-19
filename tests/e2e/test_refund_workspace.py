from pathlib import Path

from playwright.sync_api import expect, sync_playwright


REFUND_SCRIPT = Path("app/static/js/refunds.js")
CORE_SCRIPT = Path("app/static/js/core.js")
REFUND_STYLES = Path("app/static/css/refunds.css")
PORTAL_BASE_STYLES = Path("app/static/css/portal_base.css")
PORTAL_SCRIPT = Path("app/static/js/portal.js")
RESELLER_INBOX_STYLES = Path("app/static/css/reseller_inbox.css")
RESELLER_INBOX_SCRIPT = Path("app/static/js/reseller_inbox.js")


def test_refund_action_requires_explicit_confirmation() -> None:
    html = """
    <!doctype html><html><body>
      <form action="/complete" data-refund-confirm="Confirm the transfer was sent?">
        <button type="submit">Confirm refund completed</button>
      </form>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.route("http://meattrack.test/refunds", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("http://meattrack.test/refunds", wait_until="networkidle")
        page.add_script_tag(content=REFUND_SCRIPT.read_text(encoding="utf-8"))
        page.evaluate("document.querySelector('form').addEventListener('submit', event => event.preventDefault())")

        def dismiss(dialog):
            dialog.dismiss()

        page.on("dialog", dismiss)
        page.get_by_role("button", name="Confirm refund completed").click()
        expect(page).to_have_url("http://meattrack.test/refunds")
        expect(page.get_by_role("button", name="Confirm refund completed")).to_be_enabled()

        page.remove_listener("dialog", dismiss)
        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Confirm refund completed").click()
        expect(page.get_by_role("button", name="Confirm refund completed")).to_be_disabled()
        browser.close()


def test_dynamically_loaded_refund_form_receives_csrf_token_before_submit() -> None:
    html = """
    <!doctype html><html><head><meta name="csrf-token" content="refund-csrf-token"></head><body>
      <main data-refund-thread></main>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        page.add_script_tag(content=CORE_SCRIPT.read_text(encoding="utf-8"), type="module")
        page.locator("[data-refund-thread]").evaluate(
            """
            root => {
              root.innerHTML = `<form method="post"><button type="submit">Confirm refund</button></form>`;
              root.querySelector("form").addEventListener("submit", event => {
                event.preventDefault();
                window.submittedCsrfToken = new FormData(event.currentTarget).get("csrf_token");
              });
            }
            """
        )

        page.get_by_role("button", name="Confirm refund").click()

        assert page.evaluate("window.submittedCsrfToken") == "refund-csrf-token"
        browser.close()


def test_refund_panes_switch_asynchronously_and_support_browser_history() -> None:
    def document_for(pane: str) -> str:
        activity_class = "is-active" if pane == "activity" else ""
        conversation_class = "is-active" if pane == "conversation" else ""
        transfer_class = "is-active" if pane == "transfer" else ""
        transfer_form = "" if pane != "transfer" else """
          <form data-refund-confirm="Confirm transfer?">
            <button type="submit">Confirm refund completed</button>
          </form>
        """
        return f"""
        <!doctype html><html><head><title>{pane.title()} refund pane</title></head><body>
          <section data-portal-content>
            <section data-refund-workspace>
              <main class="refund-thread" data-refund-thread aria-live="polite">
                <nav class="refund-pane-tabs" aria-label="Refund case views">
                  <a class="{activity_class}" data-refund-pane-link data-async-filter-target="[data-refund-thread]" href="?refund=1&amp;pane=activity">Activity</a>
                  <a class="{conversation_class}" data-refund-pane-link data-async-filter-target="[data-refund-thread]" href="?refund=1&amp;pane=conversation">Conversation</a>
                  <a class="{transfer_class}" data-refund-pane-link data-async-filter-target="[data-refund-thread]" href="?refund=1&amp;pane=transfer">Record external transfer</a>
                </nav>
                <div data-refund-pane>{pane}</div>
                {transfer_form}
              </main>
            </section>
          </section>
        </body></html>
        """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        activity_html = document_for("activity")
        conversation_html = document_for("conversation")
        transfer_html = document_for("transfer")
        page.route(
            "http://meattrack.test/refunds?refund=1&pane=activity",
            lambda route: route.fulfill(body=activity_html, content_type="text/html"),
        )
        page.goto("http://meattrack.test/refunds?refund=1&pane=activity", wait_until="networkidle")
        page.evaluate(
            """
            documents => {
              window.lucide = { createIcons() {} };
              window.refundFetches = 0;
              window.fetch = async (input) => {
                window.refundFetches += 1;
                const url = new URL(String(input), window.location.href);
                const requestedPane = url.searchParams.get("pane");
                const pane = ["activity", "conversation", "transfer"].includes(requestedPane) ? requestedPane : "activity";
                return new Response(documents[pane], {
                  status: 200,
                  headers: { "Content-Type": "text/html" },
                });
              };
            }
            """,
            {"activity": activity_html, "conversation": conversation_html, "transfer": transfer_html},
        )
        page.add_script_tag(content=PORTAL_SCRIPT.read_text(encoding="utf-8"))
        page.add_script_tag(content=REFUND_SCRIPT.read_text(encoding="utf-8"))

        page.locator('[data-refund-pane-link][href*="pane=conversation"]').click()
        expect(page.locator("[data-refund-pane]")).to_have_text("conversation")
        expect(page.locator('[data-refund-pane-link][href*="pane=conversation"]')).to_be_focused()
        assert page.url.endswith("refund=1&pane=conversation")

        page.locator('[data-refund-pane-link][href*="pane=transfer"]').click()
        expect(page.locator("[data-refund-pane]")).to_have_text("transfer")
        expect(page.locator('[data-refund-pane-link][href*="pane=transfer"]')).to_have_class("is-active")
        expect(page.locator('[data-refund-pane-link][href*="pane=transfer"]')).to_be_focused()
        assert page.url.endswith("refund=1&pane=transfer")
        assert page.evaluate("window.refundFetches") == 2

        def dismiss(dialog):
            dialog.dismiss()

        page.on("dialog", dismiss)
        page.get_by_role("button", name="Confirm refund completed").click()
        expect(page.get_by_role("button", name="Confirm refund completed")).to_be_enabled()

        page.go_back()
        expect(page.locator("[data-refund-pane]")).to_have_text("conversation")
        page.go_back()
        expect(page.locator("[data-refund-pane]")).to_have_text("activity")
        assert page.evaluate("window.refundFetches") == 4
        browser.close()


def test_reseller_refund_conversation_spacing_does_not_overflow_mobile() -> None:
    html = """
    <!doctype html><html><head><style>
      * { box-sizing: border-box; }
      :root { --inbox-ink: #24362d; --inbox-green: #426f50; }
    </style></head><body>
      <section class="reseller-order-thread has-refund">
      <div class="reseller-order-view-tabs"><button>Activity</button><button class="is-active">Conversation</button></div>
      <div class="reseller-order-timeline" hidden>
        <article class="reseller-order-update"><span class="reseller-order-update-icon"></span><div>Refund processing started</div></article>
      </div>
        <section class="reseller-refund-chat reseller-refund-chat-panel">
          <header><span></span><div><strong>Refund conversation</strong><small>Share where the refund should be sent</small></div></header>
          <div class="reseller-refund-messages"><article><p>Please confirm your preferred account.</p></article></div>
          <form class="reseller-refund-composer"><label>Reply</label><div><textarea></textarea><button class="button">Send reply</button></div></form>
        </section>
      </section>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_content(html)
        page.add_style_tag(content=RESELLER_INBOX_STYLES.read_text(encoding="utf-8"))

        thread = page.locator(".reseller-order-thread").bounding_box()
        chat = page.locator(".reseller-refund-chat").bounding_box()
        textarea = page.locator(".reseller-refund-composer textarea").bounding_box()
        button = page.locator(".reseller-refund-composer button").bounding_box()

        assert thread and chat and textarea and button
        assert chat["x"] >= thread["x"]
        assert chat["x"] + chat["width"] <= thread["x"] + thread["width"] + 1
        assert textarea["x"] + textarea["width"] <= chat["x"] + chat["width"]
        assert button["x"] + button["width"] <= chat["x"] + chat["width"]
        browser.close()


def test_reseller_refund_activity_and_conversation_are_separate_tabs() -> None:
    html = """
    <!doctype html><html><body>
      <section data-reseller-inbox>
        <div data-reseller-chat-status><span></span><span></span></div>
        <button data-reseller-chat-refresh></button>
        <button data-reseller-inbox-tab="messages"></button>
        <button data-reseller-inbox-tab="system"></button>
        <section data-reseller-inbox-panel="messages"></section>
        <section data-reseller-inbox-panel="system" hidden></section>
        <button data-reseller-conversation-open aria-expanded="false"><span data-reseller-conversation-preview></span><time data-reseller-conversation-time></time></button>
        <span data-reseller-chat-agent></span><span data-reseller-chat-detail></span>
        <button data-reseller-message-delete hidden></button>
        <div data-reseller-chat-placeholder><h3></h3><p></p></div>
        <div data-reseller-chat-thread hidden><button data-reseller-chat-close></button></div>
        <div data-reseller-chat-messages><div data-reseller-message-empty></div></div>
        <form data-reseller-chat-form><textarea></textarea><button data-reseller-chat-send></button></form>
        <button data-system-conversation-open data-system-order-key="order-28" data-system-unread-message-ids="" aria-expanded="false">Open order</button>
        <section data-system-conversation-thread data-system-order-key="order-28" hidden>
          <button data-system-conversation-close></button>
          <div role="tablist" data-refund-order-tabs>
            <button role="tab" data-refund-order-tab="activity">Activity</button>
            <button role="tab" data-refund-order-tab="conversation">Conversation</button>
          </div>
          <div data-refund-order-panel="activity">Timeline only</div>
          <section data-refund-order-panel="conversation" hidden>Refund messages</section>
        </section>
      </section>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        page.evaluate(
            """
            () => {
              window.lucide = { createIcons() {} };
              window.fetch = async () => new Response(JSON.stringify({
                participant: { team_leader_name: "Sales leader" },
                can_message: true,
                conversation: null,
                messages: [],
              }), { status: 200, headers: { "Content-Type": "application/json" } });
            }
            """
        )
        page.add_script_tag(content=RESELLER_INBOX_SCRIPT.read_text(encoding="utf-8"))

        page.get_by_role("button", name="Open order").click()
        activity = page.locator('[data-refund-order-panel="activity"]')
        conversation = page.locator('[data-refund-order-panel="conversation"]')
        expect(activity).to_be_visible()
        expect(conversation).to_be_hidden()

        page.get_by_role("tab", name="Conversation").click()
        expect(activity).to_be_hidden()
        expect(conversation).to_be_visible()
        expect(page.get_by_role("tab", name="Conversation")).to_have_attribute("aria-selected", "true")

        page.get_by_role("tab", name="Conversation").press("ArrowLeft")
        expect(activity).to_be_visible()
        expect(page.get_by_role("tab", name="Activity")).to_be_focused()
        browser.close()


def test_refund_conversation_sends_and_receives_without_reload_or_toast() -> None:
    html = """
    <!doctype html><html><head><meta name="csrf-token" content="test-token"></head><body>
      <section data-refund-conversation data-refund-id="8" data-refund-role="owner">
        <div class="refund-message-list" data-refund-message-list role="log">
          <div class="refund-message-empty"><p>No messages yet.</p></div>
        </div>
        <form data-refund-message-form data-async-form action="/portal/owner/refunds/8/messages" method="post">
          <textarea name="message"></textarea>
          <button type="submit">Send</button>
          <p data-refund-message-status></p>
        </form>
      </section>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        page.evaluate(
            """
            () => {
              window.refundApiMessages = [];
              window.refundApiCalls = [];
              window.fetch = async (input, options = {}) => {
                const url = new URL(String(input), "http://meattrack.test");
                window.refundApiCalls.push({ url: url.pathname + url.search, method: options.method || "GET" });
                if (options.method === "POST") {
                  const payload = JSON.parse(options.body);
                  const message = {
                    order_refund_message_id: 2,
                    sender_role: "owner",
                    sender_name: "Finance Owner",
                    message: payload.message,
                    created_at: "2026-10-17T10:00:00+08:00",
                  };
                  window.refundApiMessages.push(message);
                  return new Response(JSON.stringify({ message }), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                  });
                }
                const afterId = Number(url.searchParams.get("after_id") || 0);
                return new Response(JSON.stringify({
                  messages: window.refundApiMessages.filter(item => item.order_refund_message_id > afterId),
                }), { status: 200, headers: { "Content-Type": "application/json" } });
              };
            }
            """
        )
        page.add_script_tag(content=REFUND_SCRIPT.read_text(encoding="utf-8"))
        original_url = page.url

        page.locator("textarea[name='message']").fill("Please use GCash ending in 4567.")
        page.get_by_role("button", name="Send").click()

        expect(page.locator('[data-refund-message-id="2"] p')).to_have_text("Please use GCash ending in 4567.")
        expect(page.locator("[data-refund-message-status]")).to_be_empty()
        assert page.url == original_url
        assert not page.locator("[data-toast]").count()

        page.evaluate(
            """
            window.refundApiMessages.push({
              order_refund_message_id: 3,
              sender_role: "reseller",
              sender_name: "Lipa Fresh Mart",
              message: "Those account details are correct.",
              created_at: "2026-10-17T10:01:00+08:00",
            });
            """
        )
        expect(page.locator('[data-refund-message-id="3"] p')).to_have_text(
            "Those account details are correct.", timeout=4500,
        )
        assert page.evaluate(
            "window.refundApiCalls.some(call => call.method === 'GET' && call.url.includes('after_id=2'))"
        )
        browser.close()


def test_refund_sections_align_with_timeline_and_form_controls_have_room() -> None:
    html = """
    <!doctype html><html><head><style>* { box-sizing: border-box; }</style></head><body class="portal-body">
      <main class="refund-thread" style="width: 800px">
        <section class="refund-timeline">
          <article class="refund-event processing"><span></span><div class="event-card"><strong>Processing refund</strong></div></article>
          <section class="refund-conversation"><header><h4>Confirm where to send the refund</h4></header></section>
        </section>
        <section class="refund-actions">
          <div class="refund-processing-forms">
            <form class="refund-complete-form">
              <header><h4>Complete refund</h4></header>
              <div class="refund-form-grid"><label>Refund method<input></label><label>Reference<input></label></div>
              <label>Internal note<textarea></textarea></label>
            </form>
            <form class="refund-fail-form"><label>Failure reason<textarea></textarea></label></form>
          </div>
        </section>
      </main>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.set_content(html)
        page.add_style_tag(content=PORTAL_BASE_STYLES.read_text(encoding="utf-8"))
        page.add_style_tag(content=REFUND_STYLES.read_text(encoding="utf-8"))

        event_card = page.locator(".event-card").bounding_box()
        conversation = page.locator(".refund-conversation").bounding_box()
        attempt = page.locator(".refund-event").bounding_box()
        inputs = page.locator(".refund-complete-form input")
        complete_form = page.locator(".refund-complete-form").bounding_box()
        fail_form = page.locator(".refund-fail-form").bounding_box()

        assert event_card and conversation and attempt and complete_form and fail_form
        assert abs(event_card["x"] - conversation["x"]) <= 1
        assert conversation["y"] - (attempt["y"] + attempt["height"]) >= 16
        assert fail_form["y"] - (complete_form["y"] + complete_form["height"]) >= 16
        assert page.locator(".refund-timeline").evaluate("element => getComputedStyle(element).paddingTop") == "20.8px"
        assert page.locator(".refund-conversation").evaluate("element => getComputedStyle(element).paddingTop") == "16px"
        assert page.locator(".refund-actions").evaluate("element => getComputedStyle(element).paddingRight") == "22.4px"
        assert page.locator(".refund-timeline").evaluate("element => getComputedStyle(element, '::before').content") == '""'
        assert page.locator(".refund-event > span").evaluate("element => getComputedStyle(element).borderRadius") == "50%"
        for index in range(inputs.count()):
            assert inputs.nth(index).bounding_box()["height"] >= 44
        browser.close()
