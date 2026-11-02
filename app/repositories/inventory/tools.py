"""Audited manual inventory tools using the same stock locks as fulfillment."""
from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

from app.business_time import business_today
from app.inventory_measurements import parse_decimal_exact, parse_whole_packs, require_capacity
from .._shared import _add_log_cursor, clean_row, fetch_all, fetch_one, get_transaction_cursor
from .allocation import _plan_fefo_allocations
from .movements import _insert_inventory_movement, _inventory_actor_name


def _reason(value):
    value = str(value).strip()
    if not value or len(value) > 500:
        raise ValueError('Enter a reason of 1 to 500 characters.')
    return value


def _actor(cur, actor):
    cur.execute("""SELECT account_id FROM accounts WHERE account_id=%s AND is_active=true
        AND activation_status='active' AND (account_type='owner' OR
        (account_type='team_leader' AND team_leader_role='inventory'))""", (actor,))
    if not cur.fetchone():
        raise ValueError('An active Inventory or Owner account is required.')


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


def _components(ids, quantities):
    if len(ids) != len(quantities) or len(ids) > 200:
        raise ValueError('Package rows are incomplete or exceed 200 products.')
    result = {}
    seen = set()
    for raw_id, raw_quantity in zip(ids, quantities):
        try:
            product_id = int(raw_id)
        except (ValueError, TypeError):
            raise ValueError('Choose a valid finished product.') from None
        if product_id in seen:
            raise ValueError('Each product can appear only once.')
        seen.add(product_id)
        if str(raw_quantity).strip() in {'', '0'}:
            continue
        result[product_id] = parse_whole_packs(raw_quantity, 'Packs per package')
        require_capacity(result[product_id], 'Packs per package')
    if not result:
        raise ValueError('Choose at least one product and its packs per package.')
    return result


def _package(cur, package_id):
    _product_lock(cur, package_id)
    cur.execute("SELECT * FROM inventory_items WHERE item_id=%s AND category='Package' AND item_type='finished_product' AND is_active=true FOR UPDATE", (package_id,))
    package = cur.fetchone()
    if not package:
        raise ValueError('Choose an active package.')
    # Legacy manufactured packages keep their history, but cannot mix with component dispatch.
    cur.execute('SELECT 1 FROM inventory_batches WHERE item_id=%s AND quantity_available>0 LIMIT 1', (package_id,))
    if cur.fetchone():
        raise ValueError('This package still has legacy batch stock. Clear it through the existing stock workflow first.')
    cur.execute('SELECT 1 FROM all_stock_reservations WHERE product_id=%s LIMIT 1', (package_id,))
    if cur.fetchone():
        raise ValueError('This package has existing reservations. Complete them before configuring component dispatch.')
    cur.execute('INSERT INTO inventory_package_settings(package_item_id) VALUES(%s) ON CONFLICT DO NOTHING', (package_id,))
    cur.execute('SELECT * FROM inventory_package_settings WHERE package_item_id=%s FOR UPDATE', (package_id,))
    return package, cur.fetchone()


def package_action(package_id, mode, ids, quantities, quantity, reason, reference, actor, *, save=False, confirmed=False):
    if mode not in {'standard', 'customized'}:
        raise ValueError('Choose Standard or Customize.')
    if save and (mode != 'standard' or not confirmed):
        raise ValueError('Confirm that the standard contents cannot be changed after saving.')
    requested = _components(ids, quantities) if save or mode == 'customized' else {}
    amount = Decimal(1) if save else parse_whole_packs(quantity, 'Number of packages')
    require_capacity(amount, 'Number of packages')
    reason = 'Save standard contents' if save else _reason(reason)
    payload = {'package': package_id, 'mode': mode, 'contents': {str(k): str(v) for k,v in requested.items()},
               'quantity': str(amount), 'reason': reason}
    with get_transaction_cursor() as cur:
        action = 'package_standard' if save else 'package_dispatch'
        if not _operation(cur, reference, actor, action, payload):
            return False
        lock_ids = set(requested)
        if not save and mode == 'standard':
            cur.execute('SELECT product_item_id FROM inventory_package_components WHERE package_item_id=%s', (package_id,))
            lock_ids.update(row['product_item_id'] for row in cur.fetchall())
        for product_id in sorted(lock_ids | {package_id}):
            _product_lock(cur, product_id)
        package, settings = _package(cur, package_id)
        if save:
            if settings['standard_locked_at']:
                raise ValueError('Standard contents are locked. Use Customize for a one-time dispatch.')
            contents = requested
        elif mode == 'standard':
            if not settings['standard_locked_at']:
                raise ValueError('Save standard contents first or choose Customize.')
            cur.execute('SELECT product_item_id,quantity_per_package FROM inventory_package_components WHERE package_item_id=%s', (package_id,))
            contents = {r['product_item_id']: r['quantity_per_package'] for r in cur.fetchall()}
        else:
            contents = requested
        cur.execute("SELECT item_id,name FROM inventory_items WHERE item_id=ANY(%s) AND item_type='finished_product' AND category IS DISTINCT FROM 'Package' AND is_active=true", (list(contents),))
        products = {r['item_id']: r['name'] for r in cur.fetchall()}
        if len(products) != len(contents) or not contents:
            raise ValueError('Contents must use active individual finished products.')
        if save:
            for product_id, count in contents.items():
                cur.execute('INSERT INTO inventory_package_components VALUES(%s,%s,%s)', (package_id, product_id, count))
            cur.execute('UPDATE inventory_package_settings SET standard_locked_at=now(),revision=revision+1 WHERE package_item_id=%s', (package_id,))
        else:
            totals = {k: v*amount for k,v in contents.items()}
            for total in totals.values():
                require_capacity(total, 'Total packs')
            allocations = _plan_fefo_allocations(cur, totals)
            actor_name = _inventory_actor_name(cur, actor)
            snapshot = [{'product_id': k, 'name': products[k], 'per_package': str(v), 'total': str(v*amount)} for k,v in contents.items()]
            cur.execute('INSERT INTO inventory_package_dispatches VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)',
                        (str(reference), package_id, package['name'], amount, mode, json.dumps(snapshot), actor_name))
            for allocation in allocations:
                cur.execute('UPDATE inventory_batches SET quantity_available=%s WHERE batch_id=%s', (allocation['after'], allocation['batch_id']))
                _insert_inventory_movement(cur, item_id=allocation['product_id'], affected_batch_id=allocation['batch_id'],
                    movement_type='stock_dispatch', quantity_delta=-allocation['take'], unit=allocation['unit'],
                    balance_before=allocation['before'], balance_after=allocation['after'], actor_account_id=actor,
                    actor_name=actor_name, note=f'Package dispatch: {package["name"]} x{amount:g} ({mode}). {reason} [ref {reference}]')
        # Record the exact names and quantities; replay validation uses the original request payload.
        _add_log_cursor(cur, actor_account_id=actor, action=action, entity_type='inventory_items', entity_id=package_id)
    return True


def tools_workspace():
    return clean_row({
        'materials': fetch_all("SELECT * FROM inventory_items WHERE item_type='raw_material' ORDER BY is_active DESC,name"),
        'packages': fetch_all("""SELECT i.*,s.standard_locked_at FROM inventory_items i LEFT JOIN inventory_package_settings s
            ON s.package_item_id=i.item_id WHERE i.category='Package' AND i.item_type='finished_product' ORDER BY i.name"""),
        'component_products': fetch_all("SELECT p.*,s.available FROM inventory_items p JOIN finished_stock_availability s ON s.item_id=p.item_id WHERE p.item_type='finished_product' AND p.category IS DISTINCT FROM 'Package' AND p.is_active=true ORDER BY p.name"),
        'package_components': fetch_all('SELECT c.*,i.name FROM inventory_package_components c JOIN inventory_items i ON i.item_id=c.product_item_id ORDER BY i.name'),
        'removed_batches': fetch_all("""SELECT b.*,i.name,a.name actor_name,COALESCE((SELECT -SUM(m.quantity_delta) FROM inventory_movements m WHERE m.affected_batch_id=b.batch_id AND m.created_at=b.removed_at AND m.movement_type='owner_correction'),0) removed_quantity FROM inventory_batches b JOIN inventory_items i ON i.item_id=b.item_id
            LEFT JOIN accounts a ON a.account_id=b.removed_by_account_id WHERE b.removed_at IS NOT NULL ORDER BY b.removed_at DESC LIMIT 100"""),
        'operations': fetch_all("SELECT o.*,a.name actor_name FROM inventory_tool_operations o JOIN accounts a ON a.account_id=o.actor_account_id ORDER BY o.created_at DESC LIMIT 50"),
        'dispatches': fetch_all('SELECT d.*,o.created_at,o.payload FROM inventory_package_dispatches d JOIN inventory_tool_operations o USING(reference) ORDER BY o.created_at DESC LIMIT 50'),
    })


def save_product_image(product_id, image, actor):
    from app.uploads import validate_product_image
    validate_product_image(image['content_type'], image['content'])
    with get_transaction_cursor() as cur:
        _actor(cur, actor)
        cur.execute("SELECT 1 FROM inventory_items WHERE item_id=%s AND item_type='finished_product' FOR UPDATE", (product_id,))
        if not cur.fetchone():
            raise ValueError('Product was not found.')
        cur.execute("""INSERT INTO inventory_product_images(product_item_id,content_type,image_data) VALUES(%s,%s,%s)
            ON CONFLICT(product_item_id) DO UPDATE SET content_type=EXCLUDED.content_type,image_data=EXCLUDED.image_data,updated_at=now()""",
                    (product_id, image['content_type'], image['content']))
        _add_log_cursor(cur, actor_account_id=actor, action='updated_product_photo', entity_type='inventory_items', entity_id=product_id)


def product_image(product_id):
    return fetch_one('SELECT content_type,image_data FROM inventory_product_images WHERE product_item_id=%s', (product_id,))
