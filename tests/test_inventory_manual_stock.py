"""Manual stock operations against disposable PostgreSQL state."""
from datetime import timedelta
from uuid import uuid4

import pytest
from app import repositories
from app.business_time import business_today
from app.database import fetch_one, get_transaction_cursor
from app.repositories.inventory import manual_stock as stock
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401

pytestmark = pytest.mark.postgres


def test_opening_deduction_replay_and_rollback(lifecycle):  # noqa: F811
    d = lifecycle
    material = repositories.add_raw_inventory_item('Manual supply', 'meat', 'kg', '10', d['inventory'])['raw_material_id']
    ref = str(uuid4())
    assert stock.adjust_raw_material(material, '2', 'Spoilage', ref, d['inventory'])
    assert not stock.adjust_raw_material(material, '2', 'Spoilage', ref, d['inventory'])
    with pytest.raises(ValueError):
        stock.adjust_raw_material(material, '99', 'Too much', str(uuid4()), d['inventory'])
    assert fetch_one('SELECT quantity_available q FROM inventory_items WHERE item_id=%s', (material,))['q'] == 8
    with pytest.raises(ValueError):
        stock.adjust_raw_material(material, '1', 'Owner attempt', str(uuid4()), d['owner'])
    ref = str(uuid4())
    args = (d['product'], 'OPEN-MANUAL', '7', business_today()+timedelta(days=60), 'Existing stock', ref, d['inventory'])
    assert stock.opening_balance(*args)
    assert not stock.opening_balance(*args)
    assert fetch_one("SELECT quantity_available q FROM inventory_batches WHERE batch_code='OPEN-MANUAL'")['q'] == 7
    assert fetch_one('SELECT quantity_available q FROM inventory_items WHERE item_id=%s', (material,))['q'] == 8
    with pytest.raises(ValueError):
        stock.opening_balance(d['product'], 'EXPIRED', '2', business_today(), 'Expired', str(uuid4()), d['inventory'])


def test_dispatch_fefo_reservations_and_material_status(lifecycle):  # noqa: F811
    d = lifecycle
    batch = d['batches'][1]
    with get_transaction_cursor() as cur:
        cur.execute("""INSERT INTO orders(order_type,reseller_id,created_by_account_id,team_leader_account_id,status,total_amount,payment_amount,
            payment_transaction_number,payment_transaction_key,payment_paid_at,payment_confirmed_at)
            SELECT 'reseller',reseller_id,created_by_account_id,team_leader_account_id,'pending',1800,1800,
            'MANUALRESERVE','MANUALRESERVE',now(),now() FROM orders WHERE order_id=%s RETURNING order_id""", (d['order'],))
        paid_order = cur.fetchone()['order_id']
        cur.execute("""INSERT INTO order_payment_proofs(order_id,uploaded_by_account_id,filename,content_type,content,size_bytes,checksum_sha256)
            SELECT %s,uploaded_by_account_id,filename,content_type,content,size_bytes,repeat('a',64) FROM order_payment_proofs WHERE order_id=%s""", (paid_order,d['order']))
        cur.execute('INSERT INTO order_stock_reservations(order_id,product_id,quantity) VALUES(%s,%s,18)', (paid_order,d['product']))
    with pytest.raises(ValueError):
        stock.dispatch_stock(d['product'], '3', 'Reserved stock', str(uuid4()), d['inventory'])
    ref = str(uuid4())
    assert stock.dispatch_stock(d['product'], '2', 'Sample packs', ref, d['inventory'])
    assert not stock.dispatch_stock(d['product'], '2', 'Sample packs', ref, d['inventory'])
    assert fetch_one('SELECT quantity_available q FROM inventory_batches WHERE batch_id=%s', (batch,))['q'] == 18
    with pytest.raises(ValueError):
        stock.remove_batch(d['product'], batch, '20', 'Stale', str(uuid4()), d['inventory'], True)
    with pytest.raises(ValueError):
        stock.remove_batch(d['product'], batch, '18', 'Reserved', str(uuid4()), d['inventory'], True)
    material = repositories.add_raw_inventory_item('Removable supply', 'meat', 'kg', '10', d['inventory'])['raw_material_id']
    stock.set_material_active(material, False, d['inventory'], True)
    assert not fetch_one('SELECT is_active FROM inventory_items WHERE item_id=%s', (material,))['is_active']
    assert repositories.list_inventory_items(category='raw_material:removed')[0]['item_id'] == material
    stock.set_material_active(material, True, d['inventory'], True)
    assert fetch_one('SELECT quantity_available q FROM inventory_items WHERE item_id=%s', (material,))['q'] == 10


def test_batch_removal_retains_history(lifecycle):  # noqa: F811
    d = lifecycle
    batch = d['batches'][1]
    assert stock.remove_batch(d['product'], batch, '20', 'Damaged batch', str(uuid4()), d['inventory'], True)
    row = fetch_one('SELECT * FROM inventory_batches WHERE batch_id=%s', (batch,))
    assert row['quantity_available'] == 0 and row['removed_at'] and row['quantity_received'] == 20
    assert fetch_one("SELECT quantity_delta FROM inventory_movements WHERE affected_batch_id=%s AND movement_type='owner_correction'", (batch,))['quantity_delta'] == -20
