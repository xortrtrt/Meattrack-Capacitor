---
tags: [meattrack, owner, portals, workflows]
verified: 2026-11-03
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
| Orders | `orders` | Reseller orders |
| Returns & refunds | `returns` | Product returns; Money refunds (`refunds`) |
| Inventory | `inventory` | Products & prices (`products`), editable pricing; Stock oversight with Inventory overview, read-only Raw materials (`materials`), Alerts (`alerts`), Expired (`expired-stock`), Replacement (`replacements`), and Movements (`movements`) |
| Resellers | `resellers` | Assignment directory |
| Sales reports | `reports` | Submitted Sales activity and legacy reports |
| Demand planning | `forecasts` | Forecast requests/results, progress, retry/cancellation |
| People & access | `accounts` | Accounts, Owner-issued reseller invitations, activation, roles, access controls, work transfer |
| Business settings | `settings` | Payment/forecast defaults, expiry warnings, public contact information |

My profile (`profile`) is linked from the sidebar account area. Notifications keep
their utility control. Product details (`product-details?product_id=...`) belongs
to Inventory. The legacy `verify` destination explains the removal of action OTPs.
Previously removed conversation, audit, security-monitoring, and system-management
pages remain unavailable.

Sources: `app/portal_config.py`, `app/capabilities.py`, `app/portals/shared.py`,
`app/templates/portals/base.html`, `app/templates/portals/owner.html`,
`app/templates/portals/owner/_workspace_header.html`.

## Reseller registration invitations (2026-11-03)

Only an active Owner can issue an invitation from People & access. The recipient
email is bound to a single-use token that expires after seven days; only its hash
is stored in PostgreSQL. The visitor registration form is unavailable without a
valid invitation. Submitting the form creates an inactive pending Reseller and
sends an OTP to the invitation email. Successful OTP verification activates the
account and reseller status and consumes the invitation in the same transaction.
The landing chatbot only shows the Business Owner's email for reseller
opportunities; negotiation and approval remain outside MEATTRACK. See
[[Roles and Access]] and [[Background Work and Integrations]].

The legacy reseller inquiry pages were removed from the Owner and Sales
Supervisor portals on 2026-11-03. Their navigation, dashboard cards, work queue
links, and decision endpoints are gone. Historical inquiry rows remain stored;
the chatbot and public form cannot create new ones. Owner-issued invitations
remain the sole reseller registration path. Sources: `app/portal_config.py`,
`app/portals/owner.py`, `app/portals/team_leader.py`,
`app/templates/portals/owner/operations.html`, and
`app/routers/live_chat.py`.

Sources: `app/portals/owner.py`, `app/repositories/reseller_invitations.py`,
`app/repositories/accounts.py`, `app/routers/reseller_registration.py`,
`app/templates/reseller_register.html`, migration
`database/migrations/050_reseller_registration_invitations.sql`.

## Supervisor account assignments (2026-11-03)

The Sales Supervisor Accounts page shows only reseller assignments, with identity
cards and an editable sales-leader selection; it does not load or render the Team
accounts directory. Supervisors can change these assignments but cannot
create portal accounts; the backend rejects direct account-creation requests.
Owner account creation remains in People & access. Sources: `app/portals/team_leader.py`,
`app/templates/portals/owner/accounts.html`, and
`app/static/css/portals/owner-directories.css`.

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

Owner inventory now provides read-only stock oversight. Inventory staff record
production, material receipts, corrections, pull-outs, disposal and physical return
custody/release. Finished stock remains searchable and paginated. Owner product
pricing stays editable and does not rewrite order values. Financial return and
refund decisions retain their separate permissions.

Sources: `app/templates/portals/owner/operations.html`, `app/portals/owner.py`,
`app/portals/product_returns.py`, `app/repositories/product_returns.py`.

## Staff handover

Owner account controls let the Owner deactivate and later reactivate active
accounts, with a required 5–500-character reason and an audit record for each
transition. Deactivation revokes active sessions. Both deactivation and
reactivation attempt to email the account holder the reason immediately; the
reactivation notice confirms that sign-in is available with the existing password. The notice remains in the outbox for
retry if delivery fails; the Owner sees whether the provider accepted it. The
Owner account directory no longer offers a password-reset email action; users
manage their passwords from their own profile. Sources:
`app/templates/portals/owner/users_access.html`,
`app/portals/owner.py`, `app/repositories/superuser.py`, `app/worker.py`, and
`app/emailer.py`. Checked 2026-11-03. See [[Roles and Access]] and
[[Background Work and Integrations]].

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
operation atomic. Account status, settings, and work transfer already write their detailed audit
within their transaction. Historical stock compensation remains transactional,
but current Owner stock permissions are read-only. Audit/monitoring pages
remain absent from navigation.

Sources: `app/portals/owner.py`, `app/repositories/superuser.py`,
`app/static/css/portals/owner-workspace.css`. Results are recorded in
[[Testing and Change Impact]].


Version-2 [[Replacement Workflow]] adds direct Supervisor assignment on submission,
so Owner fallback coverage can include new replacement requests as well as accepted
ones. Its private conversation is available through the secondary `messages`
destination; public visitor Live Chat and its Sales handoff are retired. Pull-outs now names
the existing expired-stock/custody destination, including segregated returned goods.

## Alerts and unread updates (2026-11-01)

Inventory > Alerts includes a live expiry watch using the configured warning window
and Manila expiry boundary. Expired batches link to Pull-outs; upcoming batches
link to Product Details. Finished-product warnings now use the saved business
setting `inventory.low_stock_packs`, default **10 available packs or fewer**, from
Business settings. Valid values are whole packs from 0 to 100000; zero warns only
when out of stock. The former view-only `threshold` parameter no longer changes
warning policy. The shared `finished_stock_availability` view excludes expired and
unapproved packs and subtracts all active order/replacement reservations.

Owner alerts are read-only under the 2026-11-04 stock-oversight policy. Earlier
personal review timestamps and legacy acknowledged records remain stored;
current Owner pages no longer display recorded-alert history or offer review
or closure actions. Stock alerts
show Inventory team responsibility, age in Manila dates, and current stock progress.
There is no individual alert assignment or claimed start-of-work event.

Stock risks clear automatically during worker maintenance, normally every minute.
Low-stock alerts clear above the saved threshold or when a finished product becomes
inactive; recurrence creates a new episode. A near-expiry episode closes when it
no longer applies, including transition to a separate expired-stock episode.
Expired-stock alerts remain active while expired warehouse packs remain, regardless
of quality reclassification. Physical pull-out clears warehouse quantities; disposal
of segregated holdings remains separate. Inventory shows the last successful scan; Owner omits this status section.
GET requests remain read-only and live stock risks do not depend on a running worker.

Owner HTTP review/closure actions are blocked by the read-only stock policy.
Worker reconciliation still clears managed stock risks after Inventory changes
the condition. Historical manually managed alert records remain readable. Notifications deduplicate by episode, recipient role, and
severity. Inventory receives low-stock notifications; Owner critical notifications
respect the existing setting. Migration 045 adds personal reviews, closure metadata,
the shared stock view, and active finished-product low-stock uniqueness; it preserves
history while consolidating duplicate active records.

Sidebar and contextual-tab number badges count each account's unread notifications
by validated destination across the full history, independent of the eight-item
preview. Sidebar parents include child destinations. Reading a notification or
marking all read clears the corresponding counts on the next page load; visiting
a destination alone does not mark notifications read. Counts cap visually at 99+.
The notification popup uses the browser Popover top layer to escape the scrolling
sidebar's clipping and stacking context, retaining outside-click and Escape dismissal.

Checked 2026-11-01 against `app/repositories/stock_alerts.py`,
`app/repositories/owner_insights.py`,
`app/repositories/notifications.py`, `app/portals/shared.py`, `app/portals/owner.py`,
`app/static/js/portal.js`, `app/templates/portals/_stock_alerts.html`, and the shared
navigation templates. See [[Orders and Inventory]] and [[Testing and Change Impact]].

The Alerts page presents three linked summary counts (expired batches, upcoming
expiry batches, and low-stock products), followed by expiry/replenishment worklists.
Each batch shows days remaining, the printed date, quantity, and a direct action.
Inventory retains its recorded-alert list and resolved history. Owner Alerts
omits that history, the live-inventory banner and the scan timestamp. Owner
Inventory overview also omits the physical/reserved/expired stock explanation
banner; summary cards and stock tables remain. Styling is isolated in
`app/static/css/portals/owner-alerts.css`; state transitions and warning rules remain
as described above. The saved warning level links to Business settings. Visually
checked on desktop and phone on 2026-11-01.

## Shared Inventory alert workspace (checked 2026-11-01)

Inventory now has `/portal/team-leader/alerts` in its navigation. Both portals call
`stock_alert_workspace` and render `app/templates/portals/_stock_alerts.html`, so
live expiry batches, available quantities, saved warning levels, stock episode IDs,
resolution state, and last successful reconciliation come from the same sources.
Inventory sees stock alert types only, including legacy material records; Owner-only
forecast alerts and personal review timestamps are not exposed. Inventory uses its
own stock-details and pull-out links; Owner stock views provide inspection only.
The worker's existing reconciliation remains authoritative for episode clearance;
page reads never create a second set of alerts. See [[Orders and Inventory]] and
[[Roles and Access]]. Sources: `app/repositories/stock_alerts.py`,
`app/portals/team_leader.py`, `app/portals/owner.py`, and the shared template.

Inventory Alerts layout polish (2026-11-01) preserves the warm portal palette and
three summary cards. Inventory-only CSS improves text sizes, aligns mobile metric
rows, tightens worklist spacing, emphasizes action links, and reduces excess mobile
bottom padding. Owner styling, shared alert data, and controls remain unchanged.
Sources: `app/static/css/portals/inventory-alerts.css` and the Inventory page wrapper.

Owner alert summary cards now open `/portal/owner/alerts?category=expired`,
`category=expiring`, and `category=low-stock` rather than same-page anchors
(checked 2026-11-01). Both portals share
`app/templates/portals/_stock_alert_detail.html`; each supplies its own role,
so category tabs, Refresh, Back, and stock-review links remain portal-scoped.
Owner detail styling stays in `app/static/css/portals/owner-alerts.css` and uses
full available width. Review, closure, policy, and notification behavior on the
Owner overview is unchanged. See [[Orders and Inventory]] and
[[Testing and Change Impact]]. Sources: `app/portals/owner.py`,
`app/templates/portals/owner/alerts.html`, and `tests/test_inventory_alert_workspace.py`.


## Role-dashboard intervention reads (2026-11-02)

The dashboard keeps all exception rows, including zeros, with financial detail
collapsed. Missing Sales/Supervisor coverage reads share predicates with their
linked filtered lists and include inactive assignees. Other Owner sections and
transaction rules remain unchanged. See [[Role Dashboards]] and [[Decisions]].
Sources: `app/repositories/coverage_filters.py`, `app/repositories/owner_insights.py`,
`app/templates/portals/owner/dashboard.html`.

## Read-only stock oversight (2026-11-04)

The Inventory workspace has exactly two main tabs: Products & prices and Stock
oversight. The latter groups Inventory overview, Raw materials, Alerts, Expired,
Replacement, and Movements. Batch/product and pull-out detail URLs remain readable from stock
links, without standalone main tabs or stock-changing forms. Current availability,
physical stock, reservations and immutable movement history retain their existing
queries. Owner production/receipt/correction/pull-out/disposal and physical return
custody endpoints reject direct requests; Inventory staff retain operational access.
The Owner can still edit prices and make permitted order/financial decisions.
Both Owner product lists show five products per page; overview summary totals
still span the full active catalog. Search and page clamping use the shared
`app/portals/shared.py:product_page` adapter.
Manual alert review/closure is no longer available through Owner stock views.
Sources: `app/portal_config.py`, `app/capabilities.py`, `app/portals/owner.py`,
`app/portals/product_returns.py`, `app/repositories/inventory/movements.py`,
`app/repositories/product_returns.py`, `app/repositories/superuser.py`, and
`app/templates/portals/owner/`. See [[Roles and Access]], [[Orders and Inventory]],
and [[Decisions]].


## Owner return presentation alignment (2026-11-04)

The Owner keeps its existing case dialog and authorized review/delivery actions.
Policy-2 replacements show no payment or price adjustment in the shared summary;
historical policy-1 cases retain their recorded extra-payment/difference amounts.
Converted cases show refund status and amount, with return-verification waiting
separate from settlement waiting. The list also recognizes the conversion marker.
Stock-shortage approval already stores `resolution='refund'`; the marker check is
an explicit presentation safeguard, not a change to conversion or settlement.
Owner dialogs use the existing Owner replacement conversation link and omit the
Team Leader Sales-chat link. See [[Replacement Workflow]] and
[[Returns and Financials]]. Sources: `app/templates/portals/team-leader/returns.html`,
`app/templates/portals/team-leader/_return_case_dialog.html`,
`app/templates/portals/returns/_supervisor_case_summary.html`,
`app/templates/portals/returns/_replacement_actions.html`, and
`app/repositories/replacement_workflow.py`. Rendering checks:
`tests/test_sales_case_cards.py` (policy, conversion, custody gate, conversation).


The Expired tab opens the existing read-only expired-stock and holding records.
Replacement lists original replacement requests and their current status, including
requests later converted to refunds; unrelated refund-only requests stay in Returns
& refunds. Search, status filtering and pagination happen in the repository query
before the 12-row page limit. Case links open existing authorized Owner return
details without adding stock-changing controls to oversight. See [[Replacement Workflow]].
Sources: `app/portals/owner.py:replacements_context`,
`app/repositories/product_returns.py:list_product_returns`,
`app/templates/portals/owner/replacements.html`, and
`tests/test_owner_stock_oversight.py`. Checked locally 2026-11-04.

The Owner Expired view omits the Custody history tab and timeline. The shared
Inventory staff view and stored custody movements remain available. Source:
`app/templates/portals/lifecycle/expired-stock.html`. Checked 2026-11-04.
