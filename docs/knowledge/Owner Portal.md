---
tags: [meattrack, owner, portals, workflows]
verified: 2026-10-30
---
# Owner portal

The Owner workspace puts business results, operational exposure, and missing
responsibility before intervention forms. Sales, Supervisor, and Inventory retain
their existing daily duties. Owner overrides use the existing repositories,
session checks, capabilities, reasons, and audit mechanisms. See
[[Roles and Access]], [[Architecture]], and [[Decisions]].

## Navigation

The 2026-10-30 overhaul reduces the main sidebar from 19 destinations to nine.
Detailed workspaces keep their original URLs and capabilities. Contextual tabs
keep their parent sidebar item selected.

| Main destination | Suffix under `/portal/owner/` | Contextual workspaces |
| --- | --- | --- |
| Overview | `dashboard` | Business overview; Work queues (`operations`) |
| Orders & inquiries | `orders` | Reseller orders; Reseller inquiries (`inquiries`) |
| Returns & refunds | `returns` | Product returns; Money refunds (`refunds`) |
| Inventory | `inventory` | Stock & production; Raw materials (`materials`); Batches (`batches`); Movements (`movements`); Pull-outs (`expired-stock`); Alerts (`alerts`); Products & prices (`products`) |
| Resellers | `resellers` | Assignment directory |
| Sales reports | `reports` | Submitted Sales activity and legacy reports |
| Demand planning | `forecasts` | Forecast requests/results, progress, retry/cancellation |
| People & access | `accounts` | Accounts, activation, roles, access controls, work transfer |
| Business settings | `settings` | Payment/forecast defaults, expiry warnings, public contact information |

My profile (`profile`) is linked from the sidebar account area. Notifications keep
their utility control. Product details (`product-details?product_id=...`) belongs
to Inventory. The legacy `verify` destination explains the removal of action OTPs.
Previously removed conversation, audit, security-monitoring, and system-management
pages remain unavailable.

Sources: `app/portal_config.py`, `app/capabilities.py`, `app/portals/shared.py`,
`app/templates/portals/base.html`, `app/templates/portals/owner.html`,
`app/templates/portals/owner/_workspace_header.html`.

## Overview and financial meaning

The overview shows retained sales, pending refund obligations, available finished
packs, active resellers, Owner exceptions, the reseller order pipeline, a gross-sales
chart, expiry exposure, latest Sales submissions, and cumulative financial context.

- Retained sales and money totals reuse `settlement_summary` /
  `return_settlement_totals` and the existing reconciliation view, including
  accessible walk-in and reseller orders.
- Retained sales deduct completed refunds against fulfilled sales. Cancellation
  refunds return money collected before fulfillment and have separate meaning.
- Net collections = confirmed original collections + additional collections − all
  completed refunds. Pending/processing/failed obligations are not completed deductions.
- The chart shows fulfilled **reseller** gross order value by fulfillment date,
  with daily/weekly/monthly/quarterly/yearly views. It excludes walk-in sales and
  refund deductions. An accessible table updates alongside the chart.
- Submitted activity reports select personal work by original order creation
  periods and preserve snapshots. They are not additive sales ledger rows.
  The reporting inbox links to saved documents; legacy reports retain their reader.
- Stock totals cover the full active finished-product catalog independently of
  table filtering/pagination. Physical stock includes expired/unapproved warehouse
  batches. Available stock sums eligible stock less order/replacement holds per
  product, clamped at zero per product. Expired warehouse packs and approved packs
  expiring within the configured warning window are separate measures.

These values do not establish profit, margin, or cash in a bank account. Each
aggregate SQL statement reads one consistent snapshot; the whole page combines
several reads and is not a frozen accounting report. Recent financial source rows
include walk-in orders, which are outside the reseller Orders workspace.

Connections: [[Orders and Inventory]], [[Returns and Financials]],
[[Sales Activity Reports]]. Sources: `app/repositories/owner_insights.py`,
`app/portals/owner.py`, `app/repositories/product_returns.py`,
`app/repositories/orders.py`, `app/templates/portals/owner/dashboard.html`,
`app/static/js/charts.js`.

## Work queues and intervention

Work queues show business-wide Sales/service work, stock/coverage exceptions, and
assignment workload. Counts overlap and are never added into a misleading task
total. Unpaid, paid-awaiting-review, and approved-awaiting-fulfillment orders remain
separate stages.

Owner attention highlights failed refunds, receipt mismatches, accepted returns
without an active Supervisor, and missing Sales/Supervisor coverage. The Owner-only
`returns?owner_only=1` filter includes accepted unfinished cases without an active
assigned Supervisor, including completed replacements with open customer follow-ups.
It persists when applying return status filters and retains normal case authorization.

Production, material receipts, and corrections remain explicit actions. Finished
stock is searchable/paginated; production choices include all active products.
Correction choices no longer silently stop at 250 items/batches. Price changes do
not rewrite order values. Physical pull-out, disposal, return custody, replacement
release, and money transfers retain their separate transitions.

Sources: `app/templates/portals/owner/operations.html`, `app/portals/owner.py`,
`app/portals/product_returns.py`, `app/repositories/product_returns.py`.

## Staff handover

Deactivation checks now include accepted return processing, open post-release
follow-ups, assigned unfinished refunds, and refunds actively processed by the
account. Removing Supervisor access is blocked while those duties remain.
Existing Inventory-role and final-active-Owner safeguards remain enforced.

Transfer active work moves applicable reseller, inquiry, order, refund, and
prospect-chat assignments, plus active return-processing assignments. Completed
replacements with open follow-ups also transfer. Destinations receiving return or
refund duties must be active Sales Supervisors.

An active refund attempt cannot be silently reassigned: finish it, fail it through
the existing workflow, or have an Owner use the existing takeover first. Subsequent
assignment transfer preserves the actual current processor and attempt history.

Return reassignment records a case event and an in-portal notification for the new
Supervisor in the same transaction as the transfer and Owner audit. It preserves
approvals, quote revision, custody, settlement amounts, completed history, and
original actor attribution. Workload tables include active return cases.

Sources: `app/repositories/superuser.py`, `app/repositories/accounts.py`,
`app/templates/portals/owner/users_access.html`, `tests/test_owner_overhaul.py`.

## Exports and implementation boundaries

Exports explicitly say **all records** and are independent of current page filters.
The Sales summary export includes only `report_source='team_leader'`, matching the
report inbox and preserving snapshot/legacy financial rules and CSV formula
protection. Cumulative financial context belongs on Overview rather than preceding
submitted reports or the Owner return case list.

The app remains FastAPI/Jinja2/vanilla JavaScript. Owner-only styles use existing
Rubik typography and brand gold with neutral business surfaces. Contextual tabs,
focus styles, a skip link, native dialogs, a chart table, and responsive layouts
support navigation across desktop/tablet/phone widths. Separate inquiry, material,
alert, and settings templates replace `owner/superuser.html`.

The Owner overhaul itself introduced no schema migration. The subsequent
[[Replacement Workflow]] additionally requires migration 044. Worker-dependent behavior stays described in
[[Background Work and Integrations]]. This work does not establish production
deployment, provider delivery, or real financial transfers.

Some existing Owner overrides commit their domain operation before separately
writing the detailed reason/audit record. This overhaul does not make every such
operation atomic. Account status, settings, stock compensation, and work transfer
already write their detailed audit within their transaction. Audit/monitoring pages
remain absent from navigation.

Sources: `app/portals/owner.py`, `app/repositories/superuser.py`,
`app/static/css/portals/owner-workspace.css`. Results are recorded in
[[Testing and Change Impact]].


Version-2 [[Replacement Workflow]] adds direct Supervisor assignment on submission,
so Owner fallback coverage can include new replacement requests as well as accepted
ones. Its private conversation is available through the secondary `messages`
destination; general live-chat administration remains removed. Pull-outs now names
the existing expired-stock/custody destination, including segregated returned goods.
