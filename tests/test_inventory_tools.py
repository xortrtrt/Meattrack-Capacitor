"""Stock tools preserve ledger history, reservations, expiry, and submission identity."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app import main, repositories, web_sessions
from app.business_time import business_now, business_today
from app.database import fetch_one, get_transaction_cursor
from app.repositories.inventory import tools
from app.uploads import validate_product_image
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401


def photo():
    stream = BytesIO()
    Image.new('RGB', (8, 8), 'gold').save(stream, 'PNG')
    return {'content_type': 'image/png', 'content': stream.getvalue()}


def material(d):
    return repositories.add_raw_inventory_item('Test meat', 'meat', 'kg', '10', d['inventory'])['raw_material_id']


def package(d):
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO inventory_items(item_type,name,category,unit,base_price,pack_size,pack_size_unit,pack_content_status)
            VALUES('finished_product','Trial Package','Package','pack',100,500,'g','declared') RETURNING item_id""")
        return cur.fetchone()['item_id']


def dispatch(d, package_id, *, quantity='1', packs='2', reference=None, mode='customized'):
    return tools.package_action(package_id, mode, [str(d['product'])], [packs], quantity,
                                'Stock handed out', reference or str(uuid4()), d['inventory'])


@pytest.mark.postgres
def test_deduction_precision_replay_and_concurrent_stock(lifecycle):
    d = lifecycle
    item = material(d)
    reference = str(uuid4())
    assert tools.adjust_raw_material(item, '1.125', 'Spoilage', reference, d['inventory'])
    assert not tools.adjust_raw_material(item, '1.125', 'Spoilage', reference, d['inventory'])
    with pytest.raises(ValueError, match='different details'):
        tools.adjust_raw_material(item, '2', 'Spoilage', reference, d['inventory'])
    def deduct(_):
        try:
            return tools.adjust_raw_material(item, '5', 'Wastage', str(uuid4()), d['inventory'])
        except ValueError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(deduct, range(2))).count(True) == 1
    assert float(fetch_one('SELECT quantity_available FROM inventory_items WHERE item_id=%s', (item,))['quantity_available']) == 3.875
    with pytest.raises(ValueError):
        tools.adjust_raw_material(item, '0.0001', 'Too precise', str(uuid4()), d['inventory'])


@pytest.mark.postgres
def test_material_removal_preserves_balance_and_recipe_guard(lifecycle):
    d = lifecycle
    item = material(d)
    tools.set_material_active(item, False, d['inventory'], True)
    assert not any(m['raw_material_id']==item for m in repositories.list_raw_materials())
    with pytest.raises(ValueError, match='Reactivate'):
        material(d)
    tools.set_material_active(item, True, d['inventory'], True)
    repositories.create_product_with_recipe('Recipe test', 'Pork', '0', [item], ['1'], ['kg'], '500', 'g', d['inventory'])
    with pytest.raises(ValueError, match='recipe'):
        tools.set_material_active(item, False, d['inventory'], True)
    assert fetch_one('SELECT quantity_available FROM inventory_items WHERE item_id=%s', (item,))['quantity_available'] == 10


@pytest.mark.postgres
def test_batch_archive_stale_retry_history_and_expiry(lifecycle):
    d = lifecycle
    batch = d['batches'][1]
    with pytest.raises(ValueError, match='changed'):
        tools.remove_batch(d['product'], batch, '19', 'Wrong batch', str(uuid4()), d['inventory'], True)
    with pytest.raises(ValueError, match='Pull-outs'):
        tools.remove_batch(d['product'], d['batches'][0], '10', 'Expired', str(uuid4()), d['inventory'], True)
    reference = str(uuid4())
    assert tools.remove_batch(d['product'], batch, '20', 'Damaged packaging', reference, d['inventory'], True)
    assert not tools.remove_batch(d['product'], batch, '20', 'Damaged packaging', reference, d['inventory'], True)
    row = fetch_one('SELECT * FROM inventory_batches WHERE batch_id=%s', (batch,))
    assert row['quantity_available']==0 and row['removed_at'] and row['quantity_received']==20
    assert fetch_one("SELECT count(*) n FROM inventory_movements WHERE affected_batch_id=%s AND note LIKE 'Batch removed:%%'", (batch,))['n']==1
    import psycopg2
    with pytest.raises(psycopg2.IntegrityError), get_transaction_cursor() as cur:
        cur.execute('UPDATE inventory_batches SET quantity_available=1 WHERE batch_id=%s', (batch,))


@pytest.mark.postgres
def test_batch_removal_and_package_dispatch_preserve_order_reservation(lifecycle):
    d = lifecycle
    p = package(d)
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO orders(order_type,reseller_id,created_by_account_id,team_leader_account_id,status,total_amount,
            payment_transaction_number,payment_transaction_key,payment_paid_at,payment_amount,payment_confirmed_at)
            SELECT order_type,reseller_id,created_by_account_id,team_leader_account_id,'pending',1500,
            'RESERVETEST','RESERVETEST',now(),1500,now() FROM orders WHERE order_id=%s RETURNING order_id""", (d['order'],))
        held_order = cur.fetchone()['order_id']
        cur.execute("""INSERT INTO order_payment_proofs(order_id,uploaded_by_account_id,filename,content_type,content,size_bytes,checksum_sha256)
            SELECT %s,uploaded_by_account_id,filename,content_type,'reserve-proof'::bytea,13,repeat('f',64) FROM order_payment_proofs WHERE order_id=%s""", (held_order,d['order']))
        cur.execute("INSERT INTO order_items(order_id,product_id,quantity,unit,unit_price) VALUES(%s,%s,15,'pack',100)", (held_order,d['product']))
        cur.execute('INSERT INTO order_stock_reservations(order_id,product_id,quantity) VALUES(%s,%s,15)', (held_order, d['product']))
    with pytest.raises(ValueError, match='reservations'):
        tools.remove_batch(d['product'], d['batches'][1], '20', 'Damage', str(uuid4()), d['inventory'], True)
    with pytest.raises(ValueError, match='Not enough'):
        dispatch(d, p, quantity='3')
    assert dispatch(d, p, quantity='2')
    assert fetch_one('SELECT quantity_available FROM inventory_batches WHERE batch_id=%s', (d['batches'][1],))['quantity_available']==16
    assert fetch_one('SELECT quantity FROM order_stock_reservations WHERE order_id=%s', (held_order,))['quantity']==15


@pytest.mark.postgres
def test_package_standard_lock_customize_atomic_and_replay(lifecycle):
    d = lifecycle
    p = package(d)
    tools.package_action(p, 'standard', [d['product']], ['2'], '', '', str(uuid4()), d['inventory'], save=True, confirmed=True)
    with pytest.raises(ValueError, match='locked'):
        tools.package_action(p, 'standard', [d['product']], ['3'], '', '', str(uuid4()), d['inventory'], save=True, confirmed=True)
    reference = str(uuid4())
    assert dispatch(d, p, mode='standard', reference=reference)
    assert not dispatch(d, p, mode='standard', reference=reference)
    assert dispatch(d, p, packs='3')
    assert fetch_one('SELECT quantity_per_package FROM inventory_package_components WHERE package_item_id=%s', (p,))['quantity_per_package']==2
    assert fetch_one('SELECT quantity_available FROM inventory_batches WHERE batch_id=%s', (d['batches'][1],))['quantity_available']==15
    # Expiring today remains untouched, even when another eligible batch runs out.
    with pytest.raises(ValueError, match='Not enough'):
        dispatch(d, p, packs='16')
    assert fetch_one('SELECT quantity_available FROM inventory_batches WHERE batch_id=%s', (d['batches'][0],))['quantity_available']==10
    snapshot = fetch_one('SELECT * FROM inventory_package_dispatches WHERE reference=%s', (reference,))
    assert snapshot['contents'][0]['name']=='Test Product' and float(snapshot['contents'][0]['total'])==2
    import psycopg2
    with pytest.raises(psycopg2.DatabaseError), get_transaction_cursor() as cur:
        cur.execute('UPDATE inventory_package_components SET quantity_per_package=4 WHERE package_item_id=%s', (p,))


@pytest.mark.postgres
def test_multi_component_shortfall_rolls_back_all_stock(lifecycle):
    d = lifecycle
    p = package(d)
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO inventory_items(item_type,name,category,unit,base_price)
            VALUES('finished_product','Empty product','Pork','pack',0) RETURNING item_id""")
        empty = cur.fetchone()['item_id']
    with pytest.raises(ValueError, match='Not enough'):
        tools.package_action(p,'customized',[d['product'],empty],['2','1'],'1','Test rollback',str(uuid4()),d['inventory'])
    assert fetch_one('SELECT quantity_available FROM inventory_batches WHERE batch_id=%s', (d['batches'][1],))['quantity_available']==20
    assert fetch_one('SELECT count(*) n FROM inventory_package_dispatches')['n']==0


@pytest.mark.postgres
def test_package_concurrent_dispatch_never_oversells(lifecycle):
    d = lifecycle
    p = package(d)
    def send(_):
        try:
            return dispatch(d,p,packs='15')
        except ValueError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(send,range(2))).count(True)==1
    assert fetch_one('SELECT quantity_available FROM inventory_batches WHERE batch_id=%s', (d['batches'][1],))['quantity_available']==5


@pytest.mark.postgres
def test_photo_storage_catalog_delivery_and_atomic_recipe(lifecycle):
    d = lifecycle
    item = material(d)
    image = photo()
    result = repositories.create_product_with_recipe('Photo product','Pork','0',[item],['1'],['kg'],'500','g',d['inventory'],product_photo=image)
    assert bytes(tools.product_image(result['product_id'])['image_data'])==image['content']
    tools.save_product_image(d['product'],image,d['inventory'])
    with TestClient(main.app) as client:
        response = client.get('/media/product-photo/Test%20Product')
        assert response.status_code==200 and response.content==image['content']
        assert client.get('/media/product-photo/Unknown',follow_redirects=False).status_code==307
    with pytest.raises(ValueError):
        repositories.create_product_with_recipe('Broken photo','Pork','0',[item],['1'],['kg'],'500','g',d['inventory'],product_photo={'content_type':'image/png','content':b'broken'})
    assert not fetch_one("SELECT 1 FROM inventory_items WHERE name='Broken photo'")


@pytest.mark.parametrize('content_type,content', [('image/png',b'broken'),('image/jpeg',photo()['content']),('image/png',b'x'*(2*1024*1024+1))], ids=['broken','mime-mismatch','oversize'])
def test_photo_validation(content_type,content):
    with pytest.raises(ValueError):
        validate_product_image(content_type,content)


@pytest.mark.postgres
def test_tools_pages_role_guards_and_form_submissions(lifecycle,monkeypatch):
    d = lifecycle
    item = material(d)
    p = package(d)
    session = dict(account_id=d['inventory'],role_key='team-leader',team_leader_role='inventory',
                   portal_expires_at=int(business_now().timestamp())+600)
    monkeypatch.setattr(web_sessions,'_load_session',lambda token:dict(session))
    monkeypatch.setattr(web_sessions,'_persist_session',lambda *args:None)
    with TestClient(main.app) as client:
        client.cookies.set('meattrack_session','tools-test')
        for view in ['materials','packages','removed']:
            response = client.get('/portal/team-leader/inventory-tools?view='+view)
            assert response.status_code==200, response.text[:500]
        assert client.get(f"/portal/team-leader/product-details?product_id={d['product']}").status_code==200
        response = client.post('/portal/team-leader/inventory-tools/deduct',data={'item_id':item,'quantity':'1','reason':'Spoilage','reference':str(uuid4())},follow_redirects=False)
        assert response.status_code==303
        assert fetch_one('SELECT quantity_available FROM inventory_items WHERE item_id=%s',(item,))['quantity_available']==9
        session.update(account_id=d['sales'],team_leader_role='sales')
        assert client.get('/portal/team-leader/inventory-tools').status_code in {403,404}
        assert client.post('/portal/team-leader/inventory-tools/deduct',data={}).status_code==403
        session.update(account_id=d['supervisor'],team_leader_role='sales-supervisor')
        assert client.post('/portal/team-leader/inventory-tools/package-dispatch',data={'package_id':p},follow_redirects=False).status_code in {303,403}


@pytest.mark.postgres
def test_new_package_production_is_blocked_after_configuration(lifecycle):
    d = lifecycle
    p = package(d)
    tools.package_action(p,'standard',[d['product']],['2'],'','',str(uuid4()),d['inventory'],save=True,confirmed=True)
    with pytest.raises(ValueError, match='components'):
        repositories.add_product_batch(p,'NO-PACKAGE-BATCH','1',business_today()+timedelta(days=7),actor_account_id=d['inventory'])


@pytest.mark.postgres
def test_upgrade_preserves_existing_stock_and_can_replay(strict_inventory_database):
    from pathlib import Path
    import psycopg2
    from psycopg2 import sql
    from test_inventory_postgres import _dsn_with_search_path
    schema = 'test_inventory_upgrade_' + uuid4().hex
    admin = psycopg2.connect(strict_inventory_database)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        with psycopg2.connect(_dsn_with_search_path(strict_inventory_database,schema)) as conn:
            with conn.cursor() as cur:
                baseline = Path('database/schema.sql').read_text(encoding='utf-8')
                cur.execute(baseline.split('-- Additive inventory tools;')[0])
                cur.execute("INSERT INTO inventory_items(item_type,name,category,unit,quantity_available) VALUES('raw_material','Legacy meat','meat','kg',7)")
                migration = Path('database/migrations/046_inventory_tools.sql').read_text(encoding='utf-8')
                cur.execute(migration)
                cur.execute(migration)
                cur.execute("SELECT quantity_available FROM inventory_items WHERE name='Legacy meat'")
                assert cur.fetchone()[0]==7
                cur.execute('SELECT count(*) FROM inventory_tool_operations')
                assert cur.fetchone()[0]==0
    finally:
        with admin.cursor() as cur:
            cur.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        admin.close()
