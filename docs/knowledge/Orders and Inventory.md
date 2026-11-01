---
tags: [meattrack, orders, inventory]
verified: 2026-10-30
---
# Orders and inventory

Production creates finished-product batches. Recipe-based production consumes raw
materials; batches retain their own production/receipt dates, expiration dates,
and quantities. Finished goods use whole packs; supported raw-material units use
decimal precision. Check conversion helpers before changing units.

Unpaid **To pay** orders retain their payment deadline but hold no stock. Payment
submission must cover the server-recorded order total. It locks the order/products,
checks current availability, and reserves the complete quantity atomically.
Insufficient payment cannot advance the order or create holds. If valid full-payment
evidence arrives after stock runs out, the order is rejected and a refund-review
case is queued. Supervisor/Owner must verify receipt of funds before processing it.

Order fulfillment allocates batches using FEFO (first-expiring, first-out).
Batch deductions and order-state transitions must remain transactional. Original
fulfilled allocations supply the traceability needed by [[Returns and Financials]].
Inventory movements provide an immutable audit boundary; corrections must preserve
history instead of rewriting ledger rows.

Inventory Logs show **Total stock after**: the historical whole-item balance
across all batches, calculated from stock movement deltas in timestamp/ID order
before search, category filters, sorting, or pagination. Non-sellable return
receipts do not contribute to this total. The total remains visible on phones.
Opening-balance rows are hidden from Inventory Logs, including search, counts,
and pagination, and the Opening category is absent from the page. Those rows
remain in the immutable ledger and still contribute to every historical stock
balance; Product Details retains the batch history.
The immutable movement records retain their original batch balances; batch stock
and batch movement balances remain on `/portal/team-leader/product-details`.
These historical physical-stock totals differ from availability: Finished
Products excludes expired/unapproved batches and deducts active reservations.
Sources checked 2026-10-29:
`app/repositories/inventory/allocation.py`, `app/repositories/inventory/queries.py`,
`app/templates/portals/team-leader/logs.html`, and
`tests/test_inventory_log_balances.py`. See [[Decisions]].

Expiry uses Manila business dates: a batch is blocked starting midnight on its
printed expiration date. Request-time availability checks must enforce expiry
even if the worker is unavailable. Physical expired-stock pull-out and disposal
are separate recorded operations; see [[Background Work and Integrations]].

When the expiry scan first queues a batch, Inventory Logs also shows **Moved to
pending pull-out**, the affected packs, batch, original queue timestamp, and
historical whole-product balance. This is a status change with zero stock delta;
the goods remain in the warehouse. The log projects the unique `expired_pullouts`
record, so repeated scans produce no duplicates, previously queued batches are
included, and completed pull-outs do not erase the event. Quantities at queue time
are reconstructed from immutable stock movements, not the batch's later balance.
Physical pull-out alone records the stock deduction. Sources checked 2026-10-29:
`app/worker.py`, `app/repositories/product_returns.py`,
`app/repositories/inventory/queries.py`, `app/templates/portals/team-leader/logs.html`,
and `tests/test_pending_pullout_logs.py`. See [[Decisions]].

Expired-stock pull-out forms collect quantity and remarks; their destination is
automatically **Expired holding**, assigned by the server. Staff do not enter a
holding location. Existing custody records retain their recorded locations, and
physical disposal remains a separate action. Checked 2026-10-29 against
`app/portals/product_returns.py`, `app/templates/portals/lifecycle/expired-stock.html`,
and `tests/test_expired_stock_location.py`. Returned goods continue to use
**Discarded inventory**; see [[Returns and Financials]] and [[Decisions]].

## Connected order states

Reseller history labels fulfilled orders **Completed**, retaining the stored
`fulfilled` state and original fulfillment timestamp for stock and financial
traceability. **Order received** inserts one immutable acknowledgement in
`reseller_order_receipts`; only the owning active reseller can acknowledge a
fulfilled order. It neither deducts stock nor changes the five-day return window.
Eligible return/refund/replacement actions remain available after acknowledgement.
Migration 043 adds this separate receipt record so legacy order payment records
need no updates. Checked 2026-10-30 against `app/repositories/orders.py`,
`app/portals/reseller.py`, `app/templates/portals/reseller/history.html`, and
`tests/test_conversation_order_receipts.py`. See [[Decisions]].

- Eligible unpaid reseller orders can be cancelled; they hold no stock.
- A paid pending order requires assigned Sales review; a rejection records whether
  money was actually received. Payment screenshots alone do not confirm receipt.
- Approved orders cannot currently be rejected or cancelled through this workflow.
- Fulfilled goods use Product Returns, preserving original order and batch links.
- Returned goods remain non-sellable, and replacement releases are stock movements
  without a duplicate ordinary sale.

Inventory Logs include reseller return receipts from the non-sellable custody
ledger alongside ordinary stock movements. The **Returns** filter shows the product,
quantity, original batch, reseller order, linked return case, and recorded destination
(normally **Discarded inventory** for the current receipt workflow). Older receipts
retain their recorded locations. These log rows do not add sellable stock or
duplicate the receipt ledger. Search supports destination, reseller, order, product,
and return batch; filtering and chronological ordering precede pagination.
Sources checked 2026-10-29: `app/repositories/inventory/queries.py`,
`app/portals/team_leader.py`, `app/templates/portals/team-leader/logs.html`, and
`tests/test_inventory_receipt_decision.py`. See [[Returns and Financials]].

Paid order reservations and releases have an immutable `order_reservation_events`
ledger projected into Inventory Logs and Owner movements. These events have zero
physical-stock delta and display reserved/cleared quantities separately. Fulfillment
records the actual batch deduction once and clears its reservation in the same
transaction: available stock does not fall a second time. **Total stock after**
continues to mean physical stock. Migration snapshots identify their real migration
time rather than inventing historical reservation timestamps. Existing unpaid holds
are released by migration 040; paid holds are preserved. See [[Decisions]].

Concurrent fulfillment, expiry, cancellation, and return claims interact through
locks and operation keys. Use actual PostgreSQL tests for those invariants; see
[[Testing and Change Impact]].

Sources: [orders repository](../../app/repositories/orders.py),
[inventory allocation](../../app/repositories/inventory/allocation.py),
[inventory movements](../../app/repositories/inventory/movements.py),
[measurement rules](../../app/inventory_measurements.py), [lifecycle guide](../product-lifecycle.md),
[current cancellation and return rules](../refund-return-process.md), [database guide](../../database/README.md).

Paid-reservation sources checked 2026-10-29: `app/repositories/orders.py`,
`app/repositories/inventory/queries.py`, migration 040,
`tests/test_portal_improvements.py`. The source change does not establish deployment.

The 2026-10-30 [[Owner Portal]] groups materials, batches, movements, expiry,
alerts, and pricing beneath Inventory tabs. Its summary reads the full active
finished-product catalog separately from the paginated stock table, distinguishing
physical, available, reserved, and expired stock. Production choices use all active
products; correction choices have no former 250-record cutoff. Sources:
`app/repositories/owner_insights.py`, `app/portals/owner.py`,
`app/templates/portals/owner/inventory.html`, `tests/test_owner_overhaul.py`.

Version-2 [[Replacement Workflow]] keeps payment before preparation: complete
return receipt and verified top-up precede FEFO reservations/preparation. Ready
notification does not deduct stock; Inventory release does. The Pull-outs workspace
keeps returned-goods custody separate from expired-stock records and sellable stock.
Waiting for valid restock preserves paid selections and confirmed collections.
Sources: `app/repositories/product_returns.py`,
`app/templates/portals/lifecycle/expired-stock.html`, migration 044.
