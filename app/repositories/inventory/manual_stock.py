"""Audited manual inventory tools using the same stock locks as fulfillment."""
from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID
from datetime import date

from app.business_time import business_today
from app.inventory_measurements import parse_decimal_exact, parse_whole_packs, require_capacity
from .._shared import _add_log_cursor, get_transaction_cursor
from .allocation import _plan_fefo_allocations
from .products import _batch_code_errors
from .movements import _insert_inventory_movement, _inventory_actor_name


def _reason(value):
    value = str(value).strip()
    if not value or len(value) > 500:
        raise ValueError('Enter a reason of 1 to 500 characters.')
    return value


def _actor(cur, actor):
    cur.execute("""SELECT account_id FROM accounts WHERE account_id=%s AND is_active=true
        AND activation_status='active' AND account_type='team_leader' AND team_leader_role='inventory'""", (actor,))
    if not cur.fetchone():
        raise ValueError('An active Inventory account is required.')


def _operation(cur, reference, actor, action, payload):
    try:
        reference = str(UUID(str(reference)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Reload the page before submitting this operation.') from None
    _actor(cur, actor)
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'inventory-tool:{reference}',))
    cur.execute('SELECT * FROM inventory_tool_operations WHERE reference=%s', (reference,))
    previous = cur.fetchone()
    if previous:
        if previous['actor_account_id'] != actor or previous['action'] != action or previous['payload'] != payload:
            raise ValueError('This submission was already used for different details. Reload the page.')
        return False
    cur.execute('INSERT INTO inventory_tool_operations(reference,actor_account_id,action,payload) VALUES(%s,%s,%s,%s::jsonb)',
                (reference, actor, action, json.dumps(payload)))
    return True


def _product_lock(cur, product):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'inventory-product:{product}',))


def adjust_raw_material(item_id, quantity, reason, reference, actor):
    amount = parse_decimal_exact(quantity, 'Deduction', positive=True)
    require_capacity(amount, 'Deduction')
    reason = _reason(reason)
    with get_transaction_cursor() as cur:
        if not _operation(cur, reference, actor, 'raw_deduction', {'item': item_id, 'quantity': str(amount), 'reason': reason}):
            return False
        cur.execute("SELECT * FROM inventory_items WHERE item_id=%s AND item_type='raw_material' AND is_active=true FOR UPDATE", (item_id,))
        item = cur.fetchone()
        if not item:
            raise ValueError('Choose an active raw material.')
        before = Decimal(item['quantity_available'])
        if amount > before:
            raise ValueError('Deduction exceeds available material stock.')
        cur.execute('UPDATE inventory_items SET quantity_available=quantity_available-%s WHERE item_id=%s', (amount, item_id))
        _insert_inventory_movement(cur, item_id=item_id, movement_type='raw_deduction', quantity_delta=-amount,
            unit=item['unit'], balance_before=before, balance_after=before-amount, actor_account_id=actor,
            actor_name=_inventory_actor_name(cur, actor), note=f'Raw-material deduction: {reason} [ref {reference}]')
        _add_log_cursor(cur, actor_account_id=actor, action='deducted_raw_material', entity_type='inventory_items', entity_id=item_id)
    return True


def set_material_active(item_id, active, actor, confirmed=False):
    if not confirmed:
        raise ValueError('Confirm the material status change.')
    with get_transaction_cursor() as cur:
        _actor(cur, actor)
        cur.execute("SELECT * FROM inventory_items WHERE item_id=%s AND item_type='raw_material' FOR UPDATE", (item_id,))
        item = cur.fetchone()
        if not item:
            raise ValueError('Material was not found.')
        if not active:
            cur.execute('SELECT 1 FROM product_recipes WHERE material_item_id=%s LIMIT 1', (item_id,))
            if cur.fetchone():
                raise ValueError('This material is used in a recipe and cannot be removed.')
        if item['is_active'] == active:
            return
        cur.execute('UPDATE inventory_items SET is_active=%s WHERE item_id=%s', (active, item_id))
        _add_log_cursor(cur, actor_account_id=actor, action='reactivated_raw_material' if active else 'removed_raw_material',
                        entity_type='inventory_items', entity_id=item_id)


def remove_batch(product_id, batch_id, expected_quantity, reason, reference, actor, confirmed=False):
    if not confirmed:
        raise ValueError('Confirm the batch removal.')
    reason = _reason(reason)
    expected = parse_decimal_exact(expected_quantity, 'Displayed stock', nonnegative=True)
    payload = {'product': product_id, 'batch': batch_id, 'quantity': str(expected), 'reason': reason}
    with get_transaction_cursor() as cur:
        if not _operation(cur, reference, actor, 'batch_removal', payload):
            return False
        _product_lock(cur, product_id)
        cur.execute('SELECT * FROM inventory_batches WHERE batch_id=%s AND item_id=%s FOR UPDATE', (batch_id, product_id))
        batch = cur.fetchone()
        if not batch:
            raise ValueError('Batch was not found for this product.')
        if batch['removed_at']:
            return False
        before = Decimal(batch['quantity_available'])
        if before != expected:
            raise ValueError('Batch stock changed. Reload and review the current quantity.')
        if before and batch['expiry_date'] <= business_today():
            raise ValueError('Use Pull-outs for expired stock so holding and disposal remain traceable.')
        cur.execute('SELECT 1 FROM replacement_stock_reservations WHERE batch_id=%s LIMIT 1', (batch_id,))
        if cur.fetchone():
            raise ValueError('This batch is reserved for a replacement.')
        cur.execute("""SELECT COALESCE(SUM(quantity_available),0) eligible FROM inventory_batches
            WHERE item_id=%s AND batch_id<>%s AND quality_status='approved'
            AND expiry_date>(now() AT TIME ZONE 'Asia/Manila')::date""", (product_id, batch_id))
        remaining = cur.fetchone()['eligible']
        cur.execute('SELECT COALESCE(SUM(quantity),0) held FROM all_stock_reservations WHERE product_id=%s', (product_id,))
        if before and remaining < cur.fetchone()['held']:
            raise ValueError('Removing this batch would leave insufficient stock for existing reservations.')
        cur.execute("""UPDATE inventory_batches SET quantity_available=0,removed_at=now(),
            removed_by_account_id=%s,removal_reason=%s WHERE batch_id=%s""", (actor, reason, batch_id))
        if before:
            _insert_inventory_movement(cur, item_id=product_id, affected_batch_id=batch_id,
                movement_type='owner_correction', quantity_delta=-before, unit=batch['unit'],
                balance_before=before, balance_after=Decimal(0), actor_account_id=actor,
                actor_name=_inventory_actor_name(cur, actor), note=f'Batch removed: {batch["batch_code"]}. {reason}')
        _add_log_cursor(cur, actor_account_id=actor, action='removed_product_batch', entity_type='inventory_batches', entity_id=batch_id)
    return True



@_batch_code_errors
def opening_balance(product_id, batch_code, quantity, expiry, reason, reference, actor):
    amount = parse_whole_packs(quantity)
    require_capacity(amount, 'Opening quantity')
    reason = _reason(reason)
    code = str(batch_code).strip().upper()
    if not code or len(code) > 100:
        raise ValueError('Enter a batch code of 1 to 100 characters.')
    expiry = date.fromisoformat(str(expiry))
    if expiry <= business_today():
        raise ValueError('Expiry date must be after today. Use Pull-outs for expired stock.')
    payload = {'product': product_id, 'code': code, 'quantity': str(amount), 'expiry': str(expiry), 'reason': reason}
    with get_transaction_cursor() as cur:
        if not _operation(cur, reference, actor, 'opening_balance', payload):
            return False
        _product_lock(cur, product_id)
        cur.execute("SELECT * FROM inventory_items WHERE item_id=%s AND item_type='finished_product' AND category IS DISTINCT FROM 'Package' AND is_active=true FOR UPDATE", (product_id,))
        product = cur.fetchone()
        if not product:
            raise ValueError('Choose an active finished product.')
        cur.execute('SELECT 1 FROM inventory_batches WHERE upper(batch_code)=%s', (code,))
        if cur.fetchone():
            raise ValueError('This batch code already exists. Use a separate code for each batch.')
        cur.execute("""INSERT INTO inventory_batches(item_id,batch_code,source_type,quantity_received,quantity_available,
            unit,received_date,expiry_date,quality_status) VALUES(%s,%s,'direct_received',%s,%s,%s,%s,%s,'approved') RETURNING batch_id""",
            (product_id, code, amount, amount, product['unit'], business_today(), expiry))
        batch_id = cur.fetchone()['batch_id']
        _insert_inventory_movement(cur, item_id=product_id, affected_batch_id=batch_id, movement_type='opening_balance',
            quantity_delta=amount, unit=product['unit'], balance_before=Decimal(0), balance_after=amount,
            actor_account_id=actor, actor_name=_inventory_actor_name(cur, actor), note=f'Opening balance: {code}. {reason}')
        _add_log_cursor(cur, actor_account_id=actor, action='added_opening_balance', entity_type='inventory_batches', entity_id=batch_id)
        from ..stock_alerts import receipt_batch_alert
        from ..superuser import business_setting
        receipt_batch_alert(cur, batch_id, int(business_setting('inventory.near_expiry_days')), int(business_setting('inventory.critical_expiry_days')))
    return True


def dispatch_stock(product_id, quantity, reason, reference, actor):
    amount = parse_whole_packs(quantity)
    require_capacity(amount, 'Quantity')
    reason = _reason(reason)
    with get_transaction_cursor() as cur:
        if not _operation(cur, reference, actor, 'stock_dispatch', {'product': product_id, 'quantity': str(amount), 'reason': reason}):
            return False
        cur.execute("SELECT name,category FROM inventory_items WHERE item_id=%s AND is_active=true",(product_id,))
        product=cur.fetchone()
        quantities={product_id:amount}
        if product and product['category']=='Package':
            cur.execute("""SELECT c.product_item_id,c.quantity_per_package FROM inventory_package_components c
                JOIN inventory_package_settings cfg ON cfg.package_item_id=c.package_item_id
                WHERE c.package_item_id=%s AND cfg.standard_locked_at IS NOT NULL ORDER BY c.product_item_id""",(product_id,))
            quantities={row['product_item_id']:row['quantity_per_package']*amount for row in cur.fetchall()}
            if not quantities:
                raise ValueError('Configure this package before deducting stock.')
            reason=f"{product['name']} × {amount:g} packages. {reason}"
        allocations = _plan_fefo_allocations(cur, quantities)
        for allocation in allocations:
            cur.execute('UPDATE inventory_batches SET quantity_available=%s WHERE batch_id=%s', (allocation['after'], allocation['batch_id']))
            _insert_inventory_movement(cur, item_id=product_id, affected_batch_id=allocation['batch_id'],
                movement_type='stock_dispatch', quantity_delta=-allocation['take'], unit=allocation['unit'],
                balance_before=allocation['before'], balance_after=allocation['after'], actor_account_id=actor,
                actor_name=_inventory_actor_name(cur, actor), note=f'Stock dispatch: {reason} [ref {reference}]')
        _add_log_cursor(cur, actor_account_id=actor, action='dispatched_stock', entity_type='inventory_items', entity_id=product_id)
    return True
