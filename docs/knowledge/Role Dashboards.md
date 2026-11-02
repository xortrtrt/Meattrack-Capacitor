---
tags: [meattrack, portals, dashboards]
verified: 2026-11-02
---
# Role-focused dashboards

`app/repositories/portal_dashboard.py` provides bounded, read-only summaries.
`dashboard_queues.py` shares scopes and eligibility SQL with linked return lists.
Aggregate counts span all matching records independently of preview limits.
Normal guards precede derived-filter validation. GETs do not move stock, record
payments, write workflow events or mark conversations read.

The supplied Dashboard Suite sets the common cream/white/gold direction. Inventory
uses its stock-status/expiry and movement/task layout; Supervisor uses its case/
message and order-summary/shortcut layout. Root `DESIGN.md` and `UX-CONTRACT.md`
record runtime mapping and presentation/navigation contracts. Other sections and
sidebar navigation retain existing layouts.

## Definitions

- Reseller: own to-pay/pending/approved orders and eligible offer/payment actions;
  five recent orders and three most-ordered products from fulfilled quantities.
  Recorded line totals remain separate from current catalog prices.
- Sales Leader: assigned paid review/fulfillment, initial return review, mismatches,
  unread conversations and transferred cases waiting on Supervisor/Owner.
- Supervisor: canonical processing queues, open follow-ups after completion,
  delivery after Inventory release, and unresolved inquiries (pending/assigned/
  contacted). Open cases count each authorized unresolved return or open-follow-up
  case once. Pending refunds and failed refunds remain separate. All seven order
  statuses partition assigned reseller orders, all time.
- Inventory: live expired warehouse batches before worker pull-out episodes,
  receipt checks, eligible legacy/current replacement preparation and releases,
  low stock and waiting for customer/payment/clearance. Available packs use active
  finished availability; reserved and physical finished stock remain separate.
  Low-stock KPI includes zeros; the ring partitions positive-low, zero and above-
  warning products without mixing units. Expiring KPI counts positive approved
  finished batches within the saved near-expiry window, excluding expired batches.
  Risk preview includes expired warehouse batches first and uses saved critical
  thresholds. Its quantities are physical packs before reservations.
- Owner: existing financial/headline calculations, all exception queues including
  zeros, pipeline and charts. Missing-coverage destinations share active-role
  predicates with counts; inactive Sales/Supervisor assignments count as missing
  coverage. Queues overlap and are never summed as distinct work items.

## Navigation and messages

Derived `dashboard_queue` filters: Inventory `receipts|prepare|release`, Supervisor
`delivery|followups`, Reseller `customer_action`. Wrong roles receive HTTP 400 after
normal guards. List filters use the same predicates and preserve pagination state,
with visible clear links. Supervisor returns and inquiries add `status=open`;
default views are unchanged.

Supervisor previews read at most three rows from each existing direct/replacement
inbox, then keep the newest three. Existing participant, historic-assignment and
deletion visibility rules remain authoritative. Previews never advance read
cursors. No new messaging backend, warehouse selector, task checkbox state,
online indicator or direct reorder functionality is introduced.

Sources: `app/portals/team_leader.py`, `app/portals/reseller.py`,
`app/portals/product_returns.py`, `app/repositories/portal_dashboard.py`,
`app/repositories/dashboard_queues.py`, `app/repositories/coverage_filters.py`,
`app/repositories/inquiries.py`, `app/repositories/replacement_messages.py`,
`app/reseller_messaging.py`, `tests/test_role_dashboards.py`,
`tests/test_role_dashboards_browser.py`.
See [[Roles and Access]], [[Orders and Inventory]], [[Replacement Workflow]],
[[Owner Portal]], [[Testing and Change Impact]] and [[Decisions]].
