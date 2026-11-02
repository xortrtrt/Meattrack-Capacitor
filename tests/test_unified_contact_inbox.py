"""Shared presentation preserves source records, pair identity, and case authorization."""
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import conversation_notifications, main, reseller_contact_history, reseller_messaging, web_sessions
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from app.repositories import replacement_messages
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401
from test_replacement_workflow import request

pytestmark = pytest.mark.postgres


def test_contact_history_keeps_case_boundaries_and_order_identity(lifecycle):  # noqa: F811
    d = lifecycle
    first, _ = request(d)
    second, _ = request(d)
    replacement_messages.send(first,d['supervisor'],'First case history',str(uuid4()))
    replacement_messages.send(second,d['supervisor'],'Second case history',str(uuid4()))
    history = reseller_contact_history.workspace(d['reseller'],first)
    assert {row['return_id'] for row in history['threads']} == {first,second}
    assert {row['return_id'] for row in history['messages']} == {first,second}
    card = reseller_contact_history.order_card(d['reseller'],d['order'])
    assert card['items'] and card['total_amount'] > 0
    with pytest.raises(ValueError):
        reseller_contact_history.order_card(d['sales'],d['order'])
    assert history['direct'] is None  # Regular Sales never receives private Supervisor messages.
    assert conversation_notifications.unread_summary(d['reseller'],'reseller')['unread_conversations'] == 2
    with pytest.raises(ValueError):
        reseller_contact_history.workspace(d['sales'],first)
    message_id = str(uuid4())
    first_message = reseller_messaging.send_from_reseller(d['reseller'],message_id,'Order question',order_id=d['order'])
    second_message = reseller_messaging.send_from_reseller(d['reseller'],str(uuid4()),'General question')
    assert first_message['conversation_id'] == second_message['conversation_id']
    with get_transaction_cursor() as cur:
        cur.execute("INSERT INTO orders(order_type,reseller_id,created_by_account_id,team_leader_account_id,status,total_amount,cancellation_reason,cancelled_at) SELECT order_type,reseller_id,created_by_account_id,team_leader_account_id,'cancelled',100,'Cancelled fixture order',now() FROM orders WHERE order_id=%s RETURNING order_id",(d['order'],))
        other_order = cur.fetchone()['order_id']
    third_message = reseller_messaging.send_from_reseller(d['reseller'],str(uuid4()),'Another order',order_id=other_order)
    assert third_message['conversation_id'] == first_message['conversation_id']
    assert first_message['order_id'] == d['order']
    assert reseller_messaging.send_from_reseller(d['reseller'],message_id,'Order question',order_id=d['order'])['reseller_message_id'] == first_message['reseller_message_id']
    with pytest.raises(ValueError):
        reseller_messaging.send_from_reseller(d['reseller'],message_id,'Order question')
    with pytest.raises(ValueError):
        reseller_messaging.send_from_reseller(d['reseller'],str(uuid4()),'Wrong order',order_id=999999)
    # A shared participant permits a single aggregate view, without copying history.
    with get_transaction_cursor() as cur:
        cur.execute('UPDATE resellers SET team_leader_account_id=%s WHERE team_leader_account_id=%s',(d['supervisor'],d['sales']))
    reseller_messaging.send_from_reseller(d['reseller'],str(uuid4()),'Shared direct history',order_id=d['order'])
    assert reseller_contact_history.workspace(d['reseller'],second)['direct']['messages'][-1]['content'] == 'Shared direct history'


@pytest.mark.e2e
@pytest.mark.parametrize('width',[1440,390])
@pytest.mark.parametrize('shared',[False,True])
def test_shared_contact_ui(lifecycle,monkeypatch,width,shared):  # noqa: F811
    d = lifecycle
    first, _ = request(d)
    second, _ = request(d)
    replacement_messages.send(first,d['supervisor'],'First case history',str(uuid4()))
    replacement_messages.send(second,d['supervisor'],'Second case history',str(uuid4()))
    if shared:
        with get_transaction_cursor() as cur:
            cur.execute('UPDATE resellers SET team_leader_account_id=%s WHERE team_leader_account_id=%s',(d['supervisor'],d['sales']))
    session = dict(account_id=d['reseller'],role_key='reseller',portal_expires_at=int(business_now().timestamp())+600)
    monkeypatch.setattr(web_sessions,'_load_session',lambda token:dict(session))
    monkeypatch.setattr(web_sessions,'_persist_session',lambda token,data,*args:session.update(data))
    monkeypatch.setattr(web_sessions,'_delete_session',lambda *args:None)
    output = Path('outputs/unified-inbox')
    output.mkdir(parents=True,exist_ok=True)
    with TestClient(main.app,base_url='http://unified.test') as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':width,'height':900})
        errors = []
        page.on('pageerror',lambda error:errors.append(str(error)))
        def bridge(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k not in {'host','content-length','cookie'}})
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','transfer-encoding','set-cookie'}})
        page.route('http://unified.test/**',bridge)
        page.goto('http://unified.test/portal/reseller/inbox',wait_until='networkidle')
        expect(page.locator('[data-contact-id]:visible')).to_have_count(1)
        page.get_by_role('searchbox',name='Search conversations').fill(f'Return #{first}')
        expect(page.locator('[data-contact-id]:visible')).to_have_count(1)
        page.get_by_role('searchbox',name='Search conversations').fill('')
        expect(page.locator('[data-inbox-row="system"]:visible')).to_have_count(0)
        page.locator('[data-reseller-inbox-tab="system"]').click()
        expect(page.locator('[data-inbox-row="system"]:visible')).to_have_count(1)
        page.locator('[data-reseller-inbox-tab="all"]').click()
        expect(page.locator('[data-inbox-row="system"]:visible')).to_have_count(0)
        page.screenshot(path=str(output/f'list-{width}.png'),full_page=True)
        page.locator('[data-contact-id]:visible').click()
        expect(page).to_have_url('http://unified.test/portal/reseller/inbox')
        log = page.locator('[data-contact-log]')
        expect(log).to_contain_text('First case history')
        expect(log).to_contain_text('Second case history')
        expect(log.locator('[data-inbox-order-reference]')).to_have_count(0)
        page.locator('[data-contact-case]').select_option(str(second))
        log.get_by_role('button',name=f'Preview Replacement #{first}',exact=True).first.click()
        expect(page.locator('[data-contact-summary]')).to_contain_text(f'Replacement #{first}')
        expect(page.locator('[data-contact-case]')).to_have_value(str(second))

        page.locator('[data-contact-case]').select_option(str(first))
        page.locator('#contact-message').fill('Reply on first case')
        page.locator('#contact-message').press('Enter')
        expect(log).to_contain_text('Reply on first case')
        assert fetch_one('SELECT return_id FROM return_conversation_messages WHERE content=%s',('Reply on first case',))['return_id'] == first
        if width < 761:
            page.locator('[data-contact-back]').click()
        page.locator('[data-reseller-inbox-tab="messages"]').click()
        if shared:
            page.locator('[data-contact-id]:visible').click()
        else:
            page.locator('[data-reseller-conversation-open]').click()
        order_selector = page.locator('[data-contact-regarding]' if shared else '[data-direct-order]')
        message_input = page.locator('#contact-message' if shared else '#reseller-chat-message')
        feed = page.locator('[data-contact-log]' if shared else '[data-reseller-chat-messages]')
        card = page.locator('[data-contact-stage] [data-order-inquiry-card]' if shared else '[data-reseller-chat-thread] [data-order-inquiry-card]')
        expect(order_selector).to_have_value('')
        order_selector.select_option(f'order:{d["order"]}' if shared else str(d['order']))
        expect(card).to_contain_text('View order details')
        expect(card.locator('.inbox-order-more')).not_to_have_attribute('open','')
        card.get_by_text('More order details',exact=True).click()
        expect(card.locator('.inbox-order-more')).to_have_attribute('open','')
        card.get_by_text('More order details',exact=True).click()
        stage = page.locator('[data-contact-stage]' if shared else '[data-reseller-chat-thread]')
        assert order_selector.evaluate("el => el.closest('.inbox-compose-area') !== null")
        attempts = []
        def fail_once(route):
            if route.request.method != 'POST':
                route.fallback()
                return
            import json
            attempts.append(json.loads(route.request.post_data)['client_message_id'])
            if len(attempts) == 1:
                route.fulfill(status=503,json={'message':'Send interrupted. Retry your message.'})
            else:
                route.fallback()
        page.route('**/api/portal/reseller/inbox/messages',fail_once)
        message_input.fill('Order-context reply')
        message_input.press('Enter')
        expect(message_input).to_have_value('Order-context reply')
        expect(order_selector).to_have_value(f'order:{d["order"]}' if shared else str(d['order']))
        expect(message_input).to_be_enabled()
        message_input.press('Enter')
        expect(feed).to_contain_text('Order-context reply')
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        page.unroute('**/api/portal/reseller/inbox/messages',fail_once)
        expect(page.locator('[data-direct-filter], [data-contact-filter]')).to_have_count(0)
        expect(order_selector).to_have_value('')
        expect(card).to_be_hidden()
        message_input.fill('General inquiry after order')
        message_input.press('Enter')
        expect(feed).to_contain_text('General inquiry after order')
        assert feed.locator('.reseller-chat-message.is-grouped').count() > 0
        assert fetch_one('SELECT order_id FROM reseller_messages WHERE content=%s',('General inquiry after order',))['order_id'] is None
        expect(feed.locator('article').filter(has_text='General inquiry after order').locator('[data-inbox-order-reference]')).to_have_count(0)
        expect(feed.locator('article [data-inbox-order-reference]')).to_have_count(0)
        reference = feed.locator('.inbox-order-event').last
        assert reference.evaluate("el => Math.abs((el.querySelector('button').getBoundingClientRect().left + el.querySelector('button').getBoundingClientRect().right)/2 - (el.getBoundingClientRect().left + el.getBoundingClientRect().right)/2) < 2")
        feed.locator('[data-inbox-order-reference]').last.click()
        expect(order_selector).to_have_value('')
        expect(card).to_contain_text(f'Order #{d["order"]}')
        expect(stage.locator('.message-delete-button')).to_be_hidden()
        stage.locator('.inbox-conversation-actions > summary').click()
        expect(stage.locator('.message-delete-button')).to_be_visible()
        page.keyboard.press('Escape')
        expect(stage.locator('.message-delete-button')).to_be_hidden()
        page.screenshot(path=str(output/f'order-card-{shared}-{width}.png'),full_page=True)
        card.get_by_role('button',name='Clear order selection').click()
        expect(card).to_be_hidden()
        leader = d['supervisor'] if shared else d['sales']
        for number in range(16):
            reseller_messaging.send_from_leader(leader,d['reseller'],str(uuid4()),f'History message {number}. ' + 'Delivery coordination details. ' * 4)
        expect(feed).to_contain_text('History message 15.',timeout=10000)
        feed.evaluate('el => el.scrollTop = 0')
        reseller_messaging.send_from_leader(leader,d['reseller'],str(uuid4()),'New reply while reading older messages')
        expect(feed).to_contain_text('New reply while reading older messages',timeout=10000)
        assert feed.evaluate('el => el.scrollTop') < 20
        feed.evaluate('el => el.scrollTop = el.scrollHeight')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width < 761:
            page.locator('[data-contact-back]' if shared else '[data-reseller-chat-close]').click()
        page.locator('[data-reseller-inbox-tab="replacements"]').click()
        page.locator('[data-contact-id]:visible').click()
        page.screenshot(path=str(output/f'chat-{width}.png'),full_page=True)
        assert page.locator('[data-contact-form]').evaluate('el => el.getBoundingClientRect().bottom <= innerHeight')
        page.goto(f'http://unified.test/portal/reseller/inbox?inquire_order={d["order"]}',wait_until='networkidle')
        expect(card).to_be_visible()
        expect(order_selector).to_have_value(f'order:{d["order"]}' if shared else str(d['order']))
        assert not errors, errors
        browser.close()
