"""Component bundles with immutable order snapshots and exact-cent prices."""

from decimal import Decimal, ROUND_DOWN
import json

from .._shared import fetch_all, clean_row, get_transaction_cursor, _add_log_cursor
from app.inventory_measurements import parse_whole_packs


REFERENCE_PRODUCTS = (
    "Cheesy Overload Sausage",
    "Deli Beef",
    "Pork Rebusado",
    "Chicken Rebusado",
    "Bacon",
    "Hungarian Sausage",
    "Pork Garlic Longganisa",
    "Spicy Garlic Longganisa",
    "Triple Garlic Longganisa",
    "Triple Spicy",
    "Beef Longganisa",
    "Tocino Ala Eh",
    "Chicken Tocino",
    "Pork Tapa",
    "Beef Tapa Ala Eh",
    "Sliced Ham",
)
REFERENCE_PACKAGES = {
    "Trial Package": [1] * 16,
    "Resellers Package": [5, 2, 2, 2, 2, 2, 6, 6, 5, 5, 5, 6, 5, 6, 6, 5],
    "Area Distributor's Package": [25] * 16,
}


def package_contents(package_id):
    return clean_row(
        fetch_all(
            """SELECT c.product_item_id product_id,i.name,i.base_price,c.quantity_per_package,
        COALESCE(s.available,0) available,i.is_active
        FROM inventory_package_components c JOIN inventory_items i ON i.item_id=c.product_item_id
        LEFT JOIN finished_stock_availability s ON s.item_id=i.item_id
        WHERE c.package_item_id=%s ORDER BY i.name""",
            (package_id,),
        )
    )


def decorate_products(products):
    """Attach inclusion copy only to packages; preserve ordinary product projections."""
    packages = [p for p in products if p.get("category") == "Package"]
    if not packages:
        return products
    ids = [p.get("product_id", p.get("item_id")) for p in packages]
    rows = fetch_all(
        """SELECT c.*,i.name FROM inventory_package_components c JOIN inventory_items i ON i.item_id=c.product_item_id
        WHERE c.package_item_id=ANY(%s) ORDER BY i.name""",
        (ids,),
    )
    for package in packages:
        components = [
            dict(r) for r in rows if r["package_item_id"] == package.get("product_id", package.get("item_id"))
        ]
        package["components"] = clean_row(components)
        package["total_packs"] = sum(r["quantity_per_package"] for r in components)
        package["display_unit"] = "package"
        package["description"] = (
            "Includes:\n" + "\n".join(f"{int(r['quantity_per_package'])} × {r['name']}" for r in components)
            if components
            else "Package contents are not configured."
        )
    return products


def expand_lines(cur, products, quantities):
    """Return ordinary stock requirements, exact priced lines, and package snapshots."""
    expanded, lines, snapshots = {}, [], []
    for product_id, count in quantities.items():
        product = products[product_id]
        if product.get("category") != "Package":
            lines.append((product_id, count, product["unit"], product["base_price"], None))
            expanded[product_id] = expanded.get(product_id, Decimal(0)) + count
            continue
        cur.execute(
            """SELECT c.product_item_id product_id,c.quantity_per_package,i.name,i.base_price,i.is_active,i.category
            FROM inventory_package_components c JOIN inventory_package_settings cfg ON cfg.package_item_id=c.package_item_id
            JOIN inventory_items i ON i.item_id=c.product_item_id WHERE c.package_item_id=%s AND cfg.standard_locked_at IS NOT NULL
            ORDER BY c.product_item_id""",
            (product_id,),
        )
        components = cur.fetchall()
        if not components or any(not c["is_active"] or c["category"] == "Package" for c in components):
            raise ValueError("Package contents must use active individual products.")
        weighted = sum(c["base_price"] * c["quantity_per_package"] for c in components)
        if weighted <= 0:
            raise ValueError("Configure individual product prices before ordering this package.")
        cents = int(product["base_price"] * 100)
        entries = []
        for c in components:
            exact = Decimal(cents) * c["base_price"] / weighted
            floor = int(exact.to_integral_value(rounding=ROUND_DOWN))
            entries.append((c, floor, exact - floor))
        remainder = cents - sum(int(c["quantity_per_package"]) * price for c, price, _ in entries)
        extras = {}
        for c, price, fraction in sorted(entries, key=lambda e: (-e[2], e[0]["product_id"])):
            extra = min(remainder, int(c["quantity_per_package"]))
            extras[c["product_id"]] = extra
            remainder -= extra
        snapshot_index = len(snapshots)
        contents = []
        for c, price, fraction in entries:
            pid = c["product_id"]
            per_package = c["quantity_per_package"]
            high = extras[pid]
            expanded[pid] = expanded.get(pid, Decimal(0)) + per_package * count
            parse_whole_packs(expanded[pid], "Total product packs")
            contents.append(
                {
                    "product_id": pid,
                    "name": c["name"],
                    "packs": str(per_package),
                    "individual_price": str(c["base_price"]),
                    "allocated_unit_price": str(Decimal(price) / 100),
                    "extra_cent_packs": high,
                }
            )
            if per_package - high:
                lines.append((pid, (per_package - high) * count, "pack", Decimal(price) / 100, snapshot_index))
            if high:
                lines.append((pid, high * count, "pack", Decimal(price + 1) / 100, snapshot_index))
        snapshots.append(
            {
                "product_id": product_id,
                "name": product["name"],
                "quantity": count,
                "unit_price": product["base_price"],
                "contents": contents,
            }
        )
    for quantity in expanded.values():
        parse_whole_packs(quantity, "Total product packs")
    return expanded, lines, snapshots


def save_order_packages(cur, order_id, snapshots):
    ids = []
    for package in snapshots:
        cur.execute(
            """INSERT INTO order_packages(order_id,package_item_id,package_name,quantity,unit_price,contents)
            VALUES(%s,%s,%s,%s,%s,%s::jsonb) RETURNING order_package_id""",
            (
                order_id,
                package["product_id"],
                package["name"],
                package["quantity"],
                package["unit_price"],
                json.dumps(package["contents"]),
            ),
        )
        ids.append(cur.fetchone()["order_package_id"])
    return ids


def configure_reference_packages(actor, *, archive_legacy=False):
    """Explicit catalog conversion; no inference of extra component stock."""
    from .manual_stock import _actor
    from .movements import _insert_inventory_movement, _inventory_actor_name

    with get_transaction_cursor() as cur:
        _actor(cur, actor)
        cur.execute(
            "SELECT item_id FROM inventory_items WHERE name=ANY(%s) ORDER BY item_id",
            (list(REFERENCE_PRODUCTS) + list(REFERENCE_PACKAGES),),
        )
        for row in cur.fetchall():
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"inventory-product:{row['item_id']}",)
            )
        cur.execute(
            "SELECT * FROM inventory_items WHERE name=ANY(%s) AND item_type=%s ORDER BY item_id FOR UPDATE",
            (list(REFERENCE_PRODUCTS) + list(REFERENCE_PACKAGES), "finished_product"),
        )
        items = {r["name"]: r for r in cur.fetchall()}
        missing = (set(REFERENCE_PRODUCTS) | set(REFERENCE_PACKAGES)) - set(items)
        if missing:
            raise ValueError("Missing catalog products: " + ", ".join(sorted(missing)))
        for name, counts in REFERENCE_PACKAGES.items():
            package = items[name]
            pid = package["item_id"]
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"inventory-product:{pid}",))
            cur.execute(
                "SELECT 1 FROM order_items oi JOIN orders o USING(order_id) WHERE oi.product_id=%s AND o.status IN ('to_pay','pending','approved') LIMIT 1",
                (pid,),
            )
            if cur.fetchone():
                raise ValueError("Complete or cancel existing orders for " + name + " before conversion.")
            cur.execute("SELECT 1 FROM all_stock_reservations WHERE product_id=%s LIMIT 1", (pid,))
            if cur.fetchone():
                raise ValueError("Existing reservations prevent conversion of " + name)
            expected = {items[n]["item_id"]: Decimal(q) for n, q in zip(REFERENCE_PRODUCTS, counts)}
            cur.execute(
                "SELECT product_item_id,quantity_per_package FROM inventory_package_components WHERE package_item_id=%s",
                (pid,),
            )
            previous = {r["product_item_id"]: r["quantity_per_package"] for r in cur.fetchall()}
            cur.execute("SELECT * FROM inventory_package_settings WHERE package_item_id=%s", (pid,))
            settings = cur.fetchone()
            if settings and settings["standard_locked_at"] and previous != expected:
                raise ValueError("Saved package contents differ: " + name)
            cur.execute(
                "SELECT * FROM inventory_batches WHERE item_id=%s AND quantity_available>0 ORDER BY batch_id FOR UPDATE",
                (pid,),
            )
            batches = cur.fetchall()
            if batches and not archive_legacy:
                raise ValueError("Confirm archival of old standalone package balances first.")
            for batch in batches:
                amount = batch["quantity_available"]
                cur.execute(
                    "UPDATE inventory_batches SET quantity_available=0,removed_at=now(),removed_by_account_id=%s,removal_reason='Reclassified as a component package; no component stock added' WHERE batch_id=%s",
                    (actor, batch["batch_id"]),
                )
                _insert_inventory_movement(
                    cur,
                    item_id=pid,
                    affected_batch_id=batch["batch_id"],
                    movement_type="owner_correction",
                    quantity_delta=-amount,
                    unit=batch["unit"],
                    balance_before=amount,
                    balance_after=Decimal(0),
                    actor_account_id=actor,
                    actor_name=_inventory_actor_name(cur, actor),
                    note="Archived standalone package balance during component conversion; component quantities unchanged.",
                )
            cur.execute("UPDATE inventory_items SET category='Package' WHERE item_id=%s", (pid,))
            cur.execute(
                "INSERT INTO inventory_package_settings(package_item_id) VALUES(%s) ON CONFLICT DO NOTHING", (pid,)
            )
            if not settings or not settings["standard_locked_at"]:
                cur.execute("DELETE FROM inventory_package_components WHERE package_item_id=%s", (pid,))
                for component, quantity in expected.items():
                    cur.execute("INSERT INTO inventory_package_components VALUES(%s,%s,%s)", (pid, component, quantity))
                cur.execute(
                    "UPDATE inventory_package_settings SET standard_locked_at=now() WHERE package_item_id=%s", (pid,)
                )
            _add_log_cursor(
                cur,
                actor_account_id=actor,
                action="configured_component_package",
                entity_type="inventory_items",
                entity_id=pid,
            )
    return True
