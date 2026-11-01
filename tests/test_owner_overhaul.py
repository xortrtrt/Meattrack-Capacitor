"""Owner overview, routing, stock scope, and complete staff handover."""

from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import main, portal_config, repositories, web_sessions, reseller_messaging
from app.business_time import business_now
from app.database import fetch_one, get_transaction_cursor
from app.repositories import product_returns as returns
from app.repositories.owner_insights import owner_business_snapshot, owner_stock_snapshot
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, receive  # noqa: F401
from test_return_workflow_handoff import approved
from test_return_customer import complete_replacement

pytestmark = pytest.mark.postgres


def supervisor():
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO accounts(account_type,team_leader_role,name,email,password_hash)
            VALUES('team_leader','sales-supervisor','New Supervisor',%s,'hash') RETURNING account_id""",
            (f"new-{uuid4().hex}@owner.test",))
        return cur.fetchone()["account_id"]


def test_return_responsibility_blocks_deactivation_and_demotion_then_transfers(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    with pytest.raises(ValueError, match="active work"):
        repositories.set_account_active(d['supervisor'], False, d['owner'], 'Staff handover')
    with pytest.raises(ValueError, match="return and refund"):
        repositories.update_team_leader_account_role(d['supervisor'], 'sales', d['owner'])
    with pytest.raises(ValueError, match="Sales Supervisor"):
        repositories.transfer_team_leader_work(d['supervisor'], d['sales'], d['owner'], 'Staff handover')
    replacement = supervisor()
    before = returns.get_product_return(case, d['owner'])
    counts = repositories.transfer_team_leader_work(d['supervisor'], replacement, d['owner'], 'Staff handover')
    after = returns.get_product_return(case, replacement)
    assert counts['product_returns'] == 1
    assert after['processing_supervisor_account_id'] == replacement
    assert after['approved_by_account_id'] == before['approved_by_account_id']
    assert after['quote_revision'] == before['quote_revision']
    assert after['status'] == before['status']
    assert fetch_one("SELECT COUNT(*) n FROM product_return_events WHERE action='processing_reassigned'")['n'] == 1
    assert any(p['account_id'] == replacement and p['open_returns'] == 1 for p in repositories.list_team_workload())
    repositories.set_account_active(d['supervisor'], False, d['owner'], 'Staff handover complete')


def test_active_transfer_cannot_be_silently_moved(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    receive(d, case)
    refund = returns.get_product_return(case, d['owner'])['refund']
    repositories.start_order_refund(refund['order_refund_id'], d['supervisor'], 'sales-supervisor')
    replacement = supervisor()
    with pytest.raises(ValueError, match="active refund transfers"):
        repositories.transfer_team_leader_work(d['supervisor'], replacement, d['owner'], 'Staff handover')
    repositories.take_over_order_refund(refund['order_refund_id'], d['owner'])
    repositories.transfer_team_leader_work(d['supervisor'], replacement, d['owner'], 'Owner now handles the transfer')
    current = repositories.get_order_refund(refund['order_refund_id'], 'owner', d['owner'])
    assert current['processing_by_account_id'] == d['owner']
    assert current['assigned_supervisor_account_id'] == replacement
    assert current['status'] == 'processing'


def test_completed_replacement_with_open_followup_is_active_work(lifecycle):  # noqa: F811
    d = lifecycle
    case = complete_replacement(d)
    reseller_messaging.send_from_reseller(d['reseller'], str(uuid4()), 'A replacement pack needs checking',
        return_id=case, report_issue=True)
    with pytest.raises(ValueError, match="active work"):
        repositories.set_account_active(d['supervisor'], False, d['owner'], 'Transfer open follow-up')
    replacement = supervisor()
    repositories.transfer_team_leader_work(d['supervisor'], replacement, d['owner'], 'Transfer open follow-up')
    after = returns.get_product_return(case, replacement)
    assert after['status'] == 'completed'
    assert after['has_open_followup']
    assert after['processing_supervisor_account_id'] == replacement


def test_owner_fallback_filter_matches_dashboard_and_preserves_role_scoping(lifecycle):  # noqa: F811
    d = lifecycle
    case = approved(d)
    assert owner_business_snapshot()['owner_returns'] == 0
    assert returns.list_product_returns(d['owner'], owner_only=True) == []
    with get_transaction_cursor() as cur:
        # Reproduce legacy/unavailable staffing without using the now-guarded admin action.
        cur.execute('UPDATE accounts SET is_active=false WHERE account_id=%s', (d['supervisor'],))
    assert owner_business_snapshot()['owner_returns'] == 1
    assert [r['return_id'] for r in returns.list_product_returns(d['owner'], owner_only=True)] == [case]
    with pytest.raises(ValueError, match='Only an Owner'):
        returns.list_product_returns(d['sales'], owner_only=True)


def test_stock_summary_covers_full_catalog_and_separates_expired_from_available(lifecycle):  # noqa: F811
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO inventory_items(item_type,category,name,unit,base_price,
            pack_size,pack_size_unit,pack_content_status)
            SELECT 'finished_product','Pork','Catalog product '||n,'pack',100,500,'g','declared'
            FROM generate_series(1,60) n""")
    snapshot = owner_stock_snapshot()['counts']
    assert snapshot['products'] == 61
    assert snapshot['physical'] == 30
    assert snapshot['expired'] == 10
    assert snapshot['available'] == 20
    assert snapshot['out_of_stock'] == 60
    assert len(repositories.list_products(page=5, page_size=12)) == 12
    assert len(repositories.list_products(active_only=True)) == 61
    assert any(b['product_id'] == d['product'] and b['expired'] for b in owner_stock_snapshot()['batches'])


def test_sales_summary_export_excludes_reseller_submissions(lifecycle):  # noqa: F811
    d = lifecycle
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO sales_reports(report_source,submitted_by_account_id,period_start,period_end,
            total_sales,total_orders) VALUES('team_leader',%s,CURRENT_DATE,CURRENT_DATE,0,0),
            ('reseller',%s,CURRENT_DATE,CURRENT_DATE,0,0)""", (d['sales'], d['reseller']))
    rows = repositories.owner_export_rows('reports')
    assert len(rows) == 1
    assert rows[0]['report_source'] == 'team_leader'


@pytest.mark.e2e
def test_owner_workspace_navigation_chart_and_responsive_pages(lifecycle, monkeypatch):  # noqa: F811
    d = lifecycle
    case = approved(d)
    with get_transaction_cursor() as cur:
        cur.execute('UPDATE product_returns SET processing_supervisor_account_id=NULL WHERE return_id=%s', (case,))
    session = dict(account_id=d['owner'], role_key='owner', portal_expires_at=int(business_now().timestamp())+1200)
    monkeypatch.setattr(web_sessions, '_load_session', lambda token: dict(session))
    monkeypatch.setattr(web_sessions, '_persist_session', lambda token, data, *args: session.update(data))
    monkeypatch.setattr(web_sessions, '_delete_session', lambda *args: None)
    output = Path('outputs/owner-overhaul')
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app, base_url='http://owner.test') as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def bridge(route):
            req = route.request
            url = urlsplit(req.url)
            response = client.request(req.method, url.path + ('?' + url.query if url.query else ''),
                content=req.post_data_buffer, headers={k:v for k,v in req.headers.items()
                    if k not in {'host','content-length','cookie'}}, follow_redirects=True)
            route.fulfill(status=response.status_code, body=response.content, headers={k:v for k,v in response.headers.items()
                if k not in {'content-length','content-encoding','transfer-encoding','set-cookie'}})

        page.route('http://owner.test/**', bridge)
        sections = [s for s, _label, _icon in portal_config.portal_nav['owner']]
        sections += ['operations','inquiries','refunds','materials','batches','movements','expired-stock','alerts','products','profile']
        for width in (1440, 768, 390):
            page.set_viewport_size({'width':width,'height':1000})
            for section in sections:
                response = page.goto(f'http://owner.test/portal/owner/{section}', wait_until='networkidle')
                assert response.status == 200, section
                assert page.locator('.portal-nav > a').count() == 9
                assert page.locator('.portal-nav > a.active').count() == (0 if section == 'profile' else 1)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (section, width)
                if section in {'dashboard','operations','inventory','accounts','returns','refunds'}:
                    page.screenshot(path=str(output/f'{section}-{width}.png'), full_page=True)
            page.goto('http://owner.test/portal/owner/dashboard', wait_until='networkidle')
            page.get_by_label('Chart period').select_option('weekly')
            expect(page.locator('[data-owner-sales-table] > tr')).to_have_count(12)
            expect(page.locator('[data-owner-sales-summary]')).to_contain_text('fulfilled orders')
            page.get_by_role('link', name='Accepted returns needing an Owner').click()
            expect(page.get_by_role('link', name='Needs Owner coverage')).to_be_visible()
            assert page.locator(f'[data-return-open="{case}"]').count() == 1
            page.get_by_label('Filter by status', exact=True).select_option('awaiting_receipt')
            page.get_by_role('button', name='Filter', exact=True).click()
            page.wait_for_load_state('networkidle')
            assert 'owner_only=1' in page.url
            assert page.locator(f'[data-return-open="{case}"]').count() == 1
            page.goto('http://owner.test/portal/owner/accounts', wait_until='networkidle')
            page.get_by_role('button', name='Manage account: Lifecycle Owner').click()
            expect(page.locator('dialog[open]')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.locator('dialog[open]')).to_have_count(0)
            if width <= 860:
                page.get_by_role('button', name='Open portal menu').click()
                expect(page.locator('.portal-sidebar')).to_have_class(re.compile('is-open'))
                page.get_by_role('button', name='Close menu', exact=True).click()
        assert not errors
        browser.close()
