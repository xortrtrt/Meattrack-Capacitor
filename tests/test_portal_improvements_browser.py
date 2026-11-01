"""Render the real app against an isolated PostgreSQL schema in Chromium."""

from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, web_sessions, reseller_messaging, repositories
from app.repositories import product_returns as returns
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from test_inventory_postgres import strict_inventory_database as strict_inventory_database
from test_product_lifecycle import lifecycle as lifecycle, supporting_photo, create_case

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_dashboards_and_internal_conversations_in_browser(lifecycle, monkeypatch):
    d = lifecycle
    session = {}
    monkeypatch.setattr(web_sessions, "_load_session", lambda token: dict(session))
    monkeypatch.setattr(web_sessions, "_persist_session", lambda *args: None)
    monkeypatch.setattr(web_sessions, "_delete_session", lambda *args: None)
    output = Path("outputs/portal-improvements")
    output.mkdir(parents=True, exist_ok=True)
    avatar_photo = supporting_photo()
    with get_transaction_cursor() as cur:
        cur.execute("UPDATE accounts SET profile_image_path='uploads/profiles/browser-photo.png' WHERE account_id IN (%s,%s)",(d["reseller"],d["sales"]))

    def sign_in(role):
        session.clear()
        session.update(
            account_id=d[role],
            role_key="reseller"
            if role == "reseller"
            else "sales-supervisor"
            if role == "supervisor"
            else "team-leader",
            team_leader_role="sales-supervisor" if role == "supervisor" else role,
            portal_expires_at=int(business_now().timestamp()) + 600,
        )

    with TestClient(main.app, base_url="http://portal-improvements.test") as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def bridge(route):
            req = route.request
            parts = urlsplit(req.url)
            if parts.path == "/static/uploads/profiles/browser-photo.png":
                route.fulfill(status=200,body=avatar_photo["content"],content_type="image/png")
                return
            response = client.request(
                req.method,
                parts.path + ("?" + parts.query if parts.query else ""),
                content=req.post_data_buffer,
                headers={k: v for k, v in req.headers.items() if k not in {"host", "content-length", "cookie"}},
                follow_redirects=True,
            )
            route.fulfill(
                status=response.status_code,
                body=response.content,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k not in {"content-length", "content-encoding", "transfer-encoding"}
                },
            )

        page.route("http://portal-improvements.test/**", bridge)
        for role in ("sales", "inventory", "supervisor"):
            sign_in(role)
            portal = "sales-supervisor" if role == "supervisor" else "team-leader"
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1000 if width == 1440 else 844})
                page.goto(f"http://portal-improvements.test/portal/{portal}/dashboard")
                page.wait_for_load_state("networkidle")
                expect(page.get_by_role("heading", name="Your work at a glance")).to_be_visible()
                page.screenshot(path=str(output / f"{role}-{width}.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"), page.evaluate(
                    "Array.from(document.querySelectorAll('*')).filter(e=>e.getBoundingClientRect().right>innerWidth+2).slice(0,10).map(e=>[e.tagName,e.className,e.getBoundingClientRect().width])"
                )

        photo = supporting_photo()
        reseller_messaging.send_from_reseller(
            d["reseller"], str(uuid4()), "Please check these products", attachments=[photo]
        )
        sign_in("sales")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto("http://portal-improvements.test/portal/team-leader/messages")
        page.wait_for_load_state("networkidle")
        expect(page.locator("[data-sales-message-placeholder]")).to_be_visible()
        expect(page.locator("[data-sales-message-thread]")).not_to_be_visible()
        expect(page.get_by_text("Select a conversation to begin",exact=True)).to_be_visible()
        expect(page.locator(".sales-message-row .internal-person-avatar img")).to_be_visible()
        for width in (1440,390):
            page.set_viewport_size({"width":width,"height":900})
            page.locator(".portal-sidebar").evaluate("element => Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => {})))")
            page.screenshot(path=str(output / f"messages-opening-{width}.png"),full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.set_viewport_size({"width":1440,"height":1000})
        expect(page.locator('.portal-nav a[href$="/messages"] [data-internal-unread]')).to_have_text("1")
        page.goto("http://portal-improvements.test/portal/team-leader/resellers")
        page.get_by_role("link", name="Message reseller").click()
        expect(page.locator("[data-sales-message-thread]")).to_be_visible()
        expect(page.locator("[data-sales-message-feed] .internal-message-attachment img")).to_be_visible()
        expect(page.locator("[data-sales-thread-avatar] img")).to_be_visible()
        page.locator("[data-sales-message-feed] .internal-message-attachment").click()
        viewer = page.locator("[data-internal-media-dialog]")
        expect(viewer).to_be_visible()
        assert len(page.context.pages) == 1
        page.keyboard.press("Escape")
        expect(viewer).not_to_be_visible()
        assert fetch_one("SELECT COUNT(*) n FROM reseller_messages")["n"] == 1
        picker = page.locator('[data-sales-message-form] input[type="file"]')
        picker.set_input_files([{"name": name, "mimeType": "image/png", "buffer": photo["content"]} for name in ("products.png","remove-this.png","remove-too.png")])
        page.get_by_role("button",name="Remove remove-this.png",exact=True).click()
        page.get_by_role("button",name="Remove remove-too.png",exact=True).click()
        expect(page.locator(".internal-media-previews figure")).to_have_count(1)
        expect(page.get_by_role("button",name="Remove attachments",exact=True)).to_have_count(0)
        page.locator("#sales-reseller-message").press("Enter")
        expect(page.locator("[data-sales-message-feed] .internal-message-attachment img")).to_have_count(2)
        expect(page.locator("[data-sales-message-feed] .is-media-only p")).to_have_count(0)
        page.locator("#sales-reseller-message").fill("First line")
        page.locator("#sales-reseller-message").press("Shift+Enter")
        page.locator("#sales-reseller-message").type("Second line")
        expect(page.locator("#sales-reseller-message")).to_have_value("First line\nSecond line")
        page.locator("#sales-reseller-message").press("Enter")
        expect(page.locator("[data-sales-message-feed] p").filter(has_text="Second line")).to_be_visible()
        video = page.evaluate("""async()=>{
            const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;
            const context=canvas.getContext('2d');context.fillStyle='red';context.fillRect(0,0,64,64);
            const stream=canvas.captureStream(10);const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'});
            const chunks=[];recorder.ondataavailable=e=>chunks.push(e.data);
            const stopped=new Promise(resolve=>recorder.onstop=resolve);recorder.start();
            await new Promise(resolve=>setTimeout(resolve,250));recorder.stop();await stopped;
            stream.getTracks().forEach(track=>track.stop());return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
        }""")
        picker.set_input_files({"name": "product-video.webm", "mimeType": "video/webm", "buffer": bytes(video)})
        page.locator('[data-sales-message-form] button[type="submit"]').click()
        expect(page.locator("[data-sales-message-feed] video")).to_have_count(1)
        page.locator("[data-sales-message-feed] .internal-message-attachment").filter(has=page.locator("video")).click()
        expect(viewer.locator("video")).to_have_attribute("controls","")
        page.get_by_role("button",name="Close attachment",exact=True).click()
        expect(viewer.locator("video")).to_have_count(0)
        page.screenshot(path=str(output / "internal-media-desktop.png"), full_page=True)

        sign_in("reseller")
        page.goto("http://portal-improvements.test/portal/reseller/history?view=all")
        page.wait_for_load_state("networkidle")
        expect(page.locator("details.order-history-card[open]")).to_have_count(0)
        page.goto("http://portal-improvements.test/portal/reseller/inbox")
        page.wait_for_load_state("networkidle")
        expect(page.locator("[data-reseller-chat-placeholder]")).to_be_visible()
        assert (
            fetch_one(
                "SELECT COUNT(*) n FROM reseller_messages WHERE sender_account_id=%s AND read_at IS NULL", (d["sales"],)
            )["n"]
            == 3
        )
        page.locator("[data-reseller-conversation-open]").click()
        expect(page.locator("[data-reseller-chat-thread]")).to_be_visible()
        expect(page.locator("[data-reseller-chat-messages] .internal-message-attachment img")).to_have_count(2)
        expect(page.locator("[data-reseller-chat-thread] [data-reseller-chat-avatar] img")).to_be_visible()
        page.locator("[data-reseller-chat-messages] .internal-message-attachment").first.click()
        expect(page.locator("[data-internal-media-dialog]")).to_be_visible()
        page.get_by_role("button",name="Close attachment",exact=True).click()
        with get_transaction_cursor() as cur:
            cur.execute("UPDATE accounts SET profile_image_path=NULL WHERE account_id=%s",(d["sales"],))
        page.goto("http://portal-improvements.test/portal/reseller/inbox")
        page.locator("[data-reseller-conversation-open]").click()
        expect(page.locator("[data-reseller-chat-thread] [data-reseller-chat-avatar] svg")).to_be_visible()
        expect(page.locator("[data-reseller-chat-thread] [data-reseller-chat-avatar] img")).to_have_count(0)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        page.locator(".portal-sidebar").evaluate("element => Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => {})))")
        page.screenshot(path=str(output / "internal-media-mobile.png"), full_page=True)
        case = create_case(d)
        returns.review_product_return(case,d["sales"],True,"Approve returned products for review")
        returns.inspect_product_return(case,d["inventory"],"complete","All returned packs and evidence match",str(uuid4()))
        fid = returns.get_product_return(case,d["supervisor"])["refund"]["order_refund_id"]
        repositories.start_order_refund(fid,d["supervisor"],"sales-supervisor")
        repositories.send_order_refund_message(fid,d["supervisor"],"sales-supervisor","Please confirm your preferred refund account.\nWe will send the payment reference after processing.")
        with get_transaction_cursor() as cur:
            cur.execute("UPDATE accounts SET profile_image_path='uploads/profiles/browser-photo.png' WHERE account_id=%s",(d["supervisor"],))
        for width in (390,1440):
            page.set_viewport_size({"width":width,"height":900})
            page.goto(f"http://portal-improvements.test/portal/reseller/inbox?refund={fid}")
            panel = page.locator("[data-refund-conversation]")
            expect(panel).to_be_visible()
            expect(panel.locator("header .internal-person-avatar img")).to_be_visible()
            expect(panel.locator("[data-refund-message-list]")).to_contain_text("preferred refund account")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert panel.evaluate("element => element.scrollWidth<=element.clientWidth+1")
            page.screenshot(path=str(output / f"refund-spacing-{width}.png"),full_page=True)
        assert not errors, errors
        browser.close()
