---
tags: [meattrack, decisions]
verified: 2026-10-28
---
# Decisions

## 2026-10-29: Hide opening balances from Inventory Logs

The user requested hiding all opening data in Inventory Logs. Exclude opening
rows from the log projection after calculating historical stock totals, and remove
the Opening category from the page. Retain the ledger rows so subsequent movements
and expiry events still report correct balances. See [[Orders and Inventory]],
`app/repositories/inventory/queries.py`, `app/portals/team_leader.py`, and
`tests/test_inventory_log_balances.py`.

## 2026-10-29: Explicit reset with staggered opening stock

The user requested clearing database data while retaining accounts and finished
products, then setting 50 starting packs per product, split into 10-pack batches
with distinct expiries. Preserve linked reseller profiles and activation tokens
to keep account access intact, as well as schema migration history. Clear the old
operational records and stock ledgers in a single maintenance transaction, then
create five opening batches and matching movements per product. The user delegated
expiry selection; use 30, 45, 60, 75, and 90 days from the reset's Manila date.
These dates are reset inputs, not verified shelf-life rules. The reusable tool
requires an explicit first expiry and defaults to rollback. See
[[Database and Operations]], [[Orders and Inventory]], and
`tools/reset_operational_data.py`.

These are existing constraints observed in source/docs, plus the knowledge-vault
setup decision. This log does not invent the original authors' intent.

| Decision | Reason or observed constraint | Connection and source |
| --- | --- | --- |
| Keep a single FastAPI/Jinja2 app | Current implementation shares public and portal infrastructure | [[Architecture]]; `app/application.py`, `README.md` |
| Preserve inventory movement history | Auditability and traceable batch deductions are enforced | [[Orders and Inventory]]; `database/README.md` |
| Show historical whole-product balances in Inventory Logs (2026-10-29) | User found batch balances unclear beside product stock. Aggregate stock deltas before filtering and pagination, exclude non-sellable return receipts, and preserve stored batch snapshots for Product Details. | [[Orders and Inventory]]; `app/repositories/inventory/queries.py`, `app/templates/portals/team-leader/logs.html`, `tests/test_inventory_log_balances.py` |
| Keep returned goods non-sellable | Returned custody must not inflate available inventory | [[Returns and Financials]]; `docs/refund-return-process.md` |
| Separate customer follow-ups from settled return work (2026-10-29) | User approved a Returns-only Sales queue, existing Sales chat, optional receipt acknowledgment, tracked replacement issues, and required public rejection/mismatch explanations. Follow-ups never reopen Inventory or settlement. | [[Sales and Reseller Returns]]; migration 038, `app/return_experience.py`, `app/repositories/return_customer.py` |
| Assign pull-out destination automatically (2026-10-29) | User removed the holding-location form field. New expired-stock portal pull-outs use Expired holding; historical locations remain intact. | [[Orders and Inventory]]; `app/portals/product_returns.py`, `tests/test_expired_stock_location.py` |
| Show the first Pending pull-out transition in Inventory Logs (2026-10-29) | User requested visibility when stock expires. Read the existing unique queue record as a zero-delta status event, retain its original timestamp and quantities, and deduct stock only at physical pull-out. | [[Orders and Inventory]], [[Background Work and Integrations]]; `app/repositories/inventory/queries.py`, `tests/test_pending_pullout_logs.py` |
| Preserve Sales approval as the settlement authority | Migration 036 records authorization. The 2026-10-29 receipt decision below clears a complete match under the existing Sales approval; legacy reconciliation can retain the explicit confirmation action. | [[Returns and Financials]]; migration 036 |
| Preserve submitted report snapshots | Later refunds adjust current retained balances without rewriting original submissions | [[Returns and Financials]]; migration 034 and `docs/sales-report-refunds.md` |
| Check migration checksums | Applied schema history must remain identifiable | [[Database and Operations]]; `tools/migrate_database.py` |
| Report personal activity by original order dates (2026-10-29) | User selected personal recorded actions rather than assignment, and original order dates rather than action dates; return review does not claim a sale. | [[Sales Activity Reports]]; `app/repositories/activity_reports.py` |
| Freeze combined activity reports at submission (2026-10-29) | User requires the Owner, CSV, Excel and print to see the same document; later outcomes require regeneration. Legacy sales reports retain their existing adjustment rules. | [[Sales Activity Reports]], [[Returns and Financials]]; migration 037, `app/report_documents.py` |
| Store project knowledge as linked local Markdown (2026-10-28) | User requested Obsidian context; agents can read the same files without a plugin or running GUI | [[Start Here]]; `AGENTS.md`, `.obsidian/` |
| Consolidate Sales return review into Refunds (2026-10-29) | Earlier combined-workspace decision, superseded by the accepted-case processor handoff below. User requested one workflow for money refunds and product returns; reuse existing review, custody, authorization, and transfer records rather than adding another ledger. Dashboard totals reuse the report settlement calculation. | [[Returns and Financials]], [[Roles and Access]]; `app/portals/refunds.py`, `app/portals/product_returns.py`, `app/portals/team_leader.py` |
| Use a complete-match or mismatch Inventory receipt decision (2026-10-29) | User rejected partial-receipt entry and a second verification step. A complete proof/quantity/batch match records fixed Discarded inventory custody, verifies, and clears the existing Sales-approved resolution atomically. A mismatch notifies Sales and blocks release. Reseller acceptance, extra-payment checks, and authorized transfer processing remain enforced. | [[Returns and Financials]], [[Roles and Access]]; `app/repositories/product_returns.py`, `app/portals/product_returns.py`, `app/templates/portals/lifecycle/_inventory_return_dialog.html` |
| Reserve only after sufficient payment (2026-10-29) | User removed unpaid holds. Payment-time locks recheck stock; shortages reject into payment verification/refund review. Fulfillment clears the hold with one physical deduction, preserving availability and traceability. | [[Orders and Inventory]]; migration 040, `app/repositories/orders.py` |
| Transfer accepted return processing to Supervisor/Owner (2026-10-29) | User limits regular leaders to the initial decision and assigned tracking. Apply to accepted unfinished cases, including open post-release follow-ups, while retaining custody, approval, and money history. | [[Returns and Financials]], [[Roles and Access]]; migration 041 |
| Remove Owner action OTPs (2026-10-29) | User requires signed-in Owner actions without extra emailed codes; capabilities, CSRF, audits, activation, and password verification remain. | [[Roles and Access]]; `app/portals/owner.py` |
| Keep internal conversation media private and read cursors bounded (2026-10-29) | User requests images/videos and unread badges for internal conversations only. Participant authorization and displayed-message cursors prevent exposing files or clearing unseen replies. | [[Background Work and Integrations]]; migration 042 |
| Record order receipt separately from fulfillment (2026-10-30) | User requires an Order received button without disabling eligible returns. Keep the original fulfillment clock and stock ledger; record a once-only reseller acknowledgement separately, while reseller history displays Completed. | [[Orders and Inventory]]; migration 043, `app/repositories/orders.py`, `tests/test_conversation_order_receipts.py` |
| Consolidate Owner navigation around business decisions (2026-10-30) | User requested a portal overhaul and explicitly allowed removing navigation. Reduce 19 sidebar destinations to nine while retaining authorized detailed URLs as contextual tabs; keep staff responsibility and explicit Owner intervention. | [[Owner Portal]], [[Roles and Access]]; `app/portal_config.py`, `app/templates/portals/owner/` |
| Keep Owner financial measures distinct (2026-10-30) | Reuse reconciliation totals for retained sales, obligations, and collections; identify the chart as gross fulfilled reseller sales and report documents as snapshots. Do not invent profit or bank balances. | [[Owner Portal]], [[Returns and Financials]], [[Sales Activity Reports]]; `app/repositories/owner_insights.py`, `app/templates/portals/owner/dashboard.html` |
| Include return processing in staff handover (2026-10-30) | Prevent stranded accepted cases/open follow-ups and assignments to staff without processing permissions. In-progress financial attempts require explicit outcome/takeover and preserve their actual processor. | [[Owner Portal]], [[Roles and Access]]; `app/repositories/superuser.py`, `app/repositories/accounts.py`, `tests/test_owner_overhaul.py` |
| Version replacement requests and move new submissions directly to Supervisor (2026-10-30) | User selected reseller product choice/comparison, an immediate private conversation, receipt before top-up, verified funds before FEFO preparation, Inventory release, and Supervisor delivery. Same-product matched packs keep original prices; extras use catalog prices, cheaper selections refund the difference, paid selections stay fixed, and existing cases keep earlier rules. | [[Replacement Workflow]], [[Returns and Financials]]; migration 044, `app/repositories/replacement_workflow.py` |

| Keep local Wi-Fi sharing opt-in (2026-11-01) | User requested access for a friend on the same Wi-Fi. A local environment override changes only the app binding; default loopback access and private database/mail ports remain. | [[Database and Operations]]; `compose.yml` |

Add new entries only when a task establishes a consequential decision. Include
the date, rationale, source/evidence, and affected notes. Do not infer deployment
status or business approval from the existence of a note.

- 2026-11-01: Replace the initial view-only low-stock threshold with one saved
  business setting (default 10 packs), following the user's confirmed choice.
  Owners review alerts independently; Inventory team retains stock responsibility,
  with no individual assignment. Stock conditions control automatic clearance;
  manual closure cannot hide an active stock risk. Keep notification reads separate.
  Migration 045 and `app/repositories/stock_alerts.py` implement these boundaries.
  See [[Owner Portal]] and [[Background Work and Integrations]].

- 2026-11-01: Connect Inventory and Owner alerts through one workspace data loader
  and shared presentation. Reuse stock episodes and saved policy; do not copy
  records or give Inventory Owner review/closure powers. Inventory dashboard
  low-stock counts use live availability so stale alert records cannot change the
  displayed product count. See [[Owner Portal]] and [[Orders and Inventory]].

## Inventory alert card destinations — 2026-11-01

Inventory's three stock-risk summary cards open category detail pages with
matching live records and inclusion rules. Use category query URLs on the existing
Alerts route to retain its authorization and navigation context, with an explicit
Back link. Share row rendering with the summary so stock quantities/actions stay
consistent. This implements the requested card drill-down without changing stock
eligibility or alert closure rules. See [[Orders and Inventory]]. Sources:
`app/portals/team_leader.py`, `app/templates/portals/_stock_alerts.html`, and
`app/templates/portals/team-leader/alert-detail.html`.

## Make expired alerts actionable before the queue scan - 2026-11-01

Read pending pull-outs from live expired batch balances and optionally join their
worker queue records. Stock Alerts already detects live expiry, so requiring a
queue row could send staff to a page without the alerted batch while the worker
was delayed. Keep GETs read-only, preserve worker-created historical queue events,
and retain the transactional pull-out/disposal operations. Batch-specific links
and live progress connect the two pages; completing a warehouse pull-out does not
require disposing of the held goods first. See [[Orders and Inventory]] and
[[Testing and Change Impact]]. Sources: `app/repositories/product_returns.py`,
`app/portals/product_returns.py`, and `tests/test_alert_pullout_connection.py`.

Owner alert card navigation - 2026-11-01: use the same category detail template
as Inventory with explicit portal-role parameters. Owner cards now open matching
lists instead of page anchors; links stay in the Owner portal. Existing review
and stock rules remain unchanged. See [[Owner Portal]]. Sources:
`app/portals/owner.py`, `app/templates/portals/_stock_alert_detail.html`.


## 2026-11-01 - Integrate standalone stock tools

Add raw-material adjustments, batch archives, package contents/dispatch, and
product photos without importing the standalone database or its expiry policy.
Use shared product locks and FEFO allocation to preserve order/replacement holds.
Keep the start-of-expiry-date block and route expired goods through Pull-outs,
preserving custody/disposal semantics. Require legacy package balances and holds
to clear before component tools are used, retaining existing history. Manual
package dispatch creates no sale; standard contents lock once, and customization
is per dispatch. Store public product photos in PostgreSQL to survive redeploys.
Sources: migration 046, `app/repositories/inventory/tools.py`,
`app/portals/inventory_tools.py`, and `app/uploads.py`. See [[Orders and Inventory]].

## 2026-11-02 — Reseller owns submitted payment details

Use the same receipt-review component for ordering, history, and version-2
replacement payments. Supervisor/Owner verifies actual funds against the stored
reseller submission rather than entering another reference or amount. This keeps
collection verification tied to the exact evidence and current quote. Legacy
version-1 reconciliation remains available. See [[Replacement Workflow]] and
[[Orders and Inventory]].

## 2026-11-02 — Include historical cases in portal receipt review

Live Supervisor case review exposed a separate version-1 dialog and image-only
submission path. Apply signed reseller receipt review and read-only staff payment
values to all portal cases. Missing historical fields require reseller resubmission;
never infer reference/amount from the amount due or ask staff to enter reseller
values. Preserve old stock/custody rules and evidence. See [[Replacement Workflow]].

## 2026-11-02 — Render login CSRF tokens before client scripts

A reported Supervisor sign-in failure exposed login forms depending on JavaScript
token injection. Render tokens server-side, prevent login-page caching, and return
a retry form for rejected browser login tokens. Keep token validation, HTTP 403,
and session rotation intact. See [[Roles and Access]].

## 2026-11-02 — Queue only actionable payment reviews

Require the reseller's current confirmed details before assigning staff payment
verification. Version-2 payment review must not depend on later stock reservation.
This aligns queue counts and next actions with the signed receipt workflow while
preserving historical version-1 reservation rules. See [[Replacement Workflow]].

## 2026-11-02 — Bind confirmation to the payment staff reviewed

Read-only payment fields must not silently confirm a newer reseller submission.
Compare a reviewed-state fingerprint under the case lock before recording funds.
Changed metadata, quote, or evidence requires a reload. See [[Replacement Workflow]].
