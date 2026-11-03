---
tags: [meattrack, returns, refunds, reporting]
verified: 2026-11-03
---
# Returns and financials

## Replacement-first policy update (2026-11-03)

New policy-2 submissions use [[Replacement Workflow]]; refund-only submissions and
cheaper replacement selections remain historical policy-1 behavior. Paid rejected
orders retain their existing refund process, including payment review when stock
is lost during order payment submission.

At Supervisor approval, eligible replacement stock is checked and reserved. A
shortage converts the replacement to a return-credit refund and notifies the
reseller; settlement waits for complete returned-goods receipt and verification.
Returned expired batches are accepted into **Expired holding**. Sufficient-stock
policy-2 replacements equal the return credit and have no payment or price-
difference refund. The submitted selection is fixed during Supervisor/Owner
approval. In stock-shortage cases the Supervisor Settlement view directs the
processor to the refund workspace and explains the returned-goods verification
gate.

Historical replacement cases with a verified extra collection refund approved
original credit plus that verified collection. `order_refunds.original_credit_amount` and
`topup_amount` sum to the outgoing amount. Original-credit obligations are capped
by original collections; top-up refunds are bound to that case's verified funds.
Only the original-credit component reduces retained original sales and report
adjustments; both components reduce net collections when the transfer completes.
Submitted report snapshots remain immutable. Conversion and stock/payment races
are covered by the PostgreSQL acceptance tests. Final regression passed all 1,027
cases with no skips/errors/failures; eight post-maintenance synthetic checks and
local empty-queue/preservation checks passed. Production was not deployed. Sources: `app/repositories/refund_integrity.py`,
`app/repositories/report_settlements.py`, `app/repositories/reporting.py`,
`app/repositories/activity_reports.py`, migration 048.
See [[Decisions]] and [[Testing and Change Impact]].

The Sales/reseller task queue, next-step messages, immutable offer comparisons,
public explanations, existing-chat links, and optional post-release follow-ups
are documented in [[Sales and Reseller Returns]]. These customer follow-ups do
not reopen Inventory custody or financial settlement.

The current return process connects reseller requests, Sales review, Inventory
custody, Sales confirmation, and existing refund processors. They share order and
return identifiers; they do not create independent financial ledgers.

## Workflow and dependencies

1. Reseller submits within 120 hours of recorded fulfillment, before the exact
   deadline, with at least one supporting photo. Existing cases keep their history.
2. Assigned Sales initially accepts or rejects quantities traced to original deliveries.
   Acceptance records a Supervisor/Owner handoff and notifies the reseller and
   processor. Rejection closes the request with a public explanation. Further Sales
   actions belong to the assigned Supervisor/Owner; the original leader tracks only.
3. Inventory compares all approved packs, batches, and supporting proof, then chooses
   **Confirm complete receipt** or **Report mismatch to Sales**. New portal receipts
   accept the complete approved request; partial quantities are rejected. A mismatch
   blocks replacement and notifies the assigned Supervisor (Owner fallback for an unavailable processor).
4. A complete match atomically records outstanding goods in **Discarded inventory**,
   verifies custody, and clears the current Sales-approved resolution. The original
   Sales approver remains the authorization source; the actual Inventory reviewer is
   recorded in an `inventory_receipt_complete` event. No new Sales confirmation click
   is required for this path. Historical receipt and verification primitives remain
   available for reconciling older cases without rewriting their records.
   Receipt decisions bind to the reviewed approval revision and quantities; a
   changed approval requires reloading before the decision is accepted.
5. A mismatch must be reviewed by Supervisor/Owner before Inventory can confirm a corrected
   complete request. Received goods stay non-sellable; the fixed holding-location
   label does not itself record physical disposal.
6. For current replacements, Supervisor approval checks and reserves eligible stock.
   A shortage converts the case to refund settlement after custody verification;
   sufficient-stock replacements equal the approved credit and involve no money.
7. Authorized processors settle cash owed using the existing refund attempts,
   transfer proof, reference, date/time, and remarks. Physical disposal is independent.

Returns never restore sellable stock. Replacement releases never create duplicate
ordinary sales. Idempotent operation keys, transaction locks, evidence uniqueness,
and payment/credit limits connect this flow to [[Orders and Inventory]] and
[[Database and Operations]]. Permissions are described in [[Roles and Access]].
Actual return receipts also appear in Inventory Logs with product, quantity, source
order/return, and the recorded non-sellable destination. The log reads custody
movements directly, including historical receipts; it does not create stock receipts.
Sources checked 2026-10-29: `app/repositories/inventory/queries.py` and
`app/templates/portals/team-leader/logs.html`.

The reseller's **My Returns** tab at `/portal/reseller/history?view=returns`
uses the same expandable ledger layout as the other history tabs, without a status
filter or standalone request button. Start requests from eligible orders in
**Fulfilled**. **Track return** opens a dialog with a status summary, returned items,
settlement details, attachments, and an event timeline. Presentation sources:
`app/templates/portals/reseller/history.html`,
`app/templates/portals/reseller/returns.html`, `app/static/css/portals/reseller.css`,
`app/templates/portals/reseller/_return_tracking_dialog.html`, and
`app/static/js/product_returns.js` (checked 2026-10-29). See [[Architecture]].

## Money has different meanings

Sales uses `/portal/team-leader/returns` for **Returns & tracking**. New requests
remain actionable by the assigned leader. Accepted cases are tracking-only and
appear under Waiting on others, or Completed after settlement/follow-ups finish.
Legacy `/refunds` links redirect to this workspace. Supervisor/Owner retain money
processing and take over offer revisions, discrepancies, additional-payment checks,
settlement authorization, and customer follow-up resolution. Migration 041 also
hands off existing accepted unfinished cases, preserving approvals, quotes, custody,
and money. Completed replacements with open follow-ups count as unfinished work.

Refund transfer proof uses the order receipt OCR engine with a token scoped to the
processor, refund, and exact file. The processor checks/corrects reference, Manila
date/time, and amount; the confirmed amount must equal the refund due. Failed OCR
retains the existing manual-correction flow. Duplicate transfer evidence/references,
current-processor permissions, and settlement eligibility still gate completion.
Completion appends one thank-you system message with amount, order and reference,
notifies the reseller, and closes the conversation as read-only. See
[[Background Work and Integrations]] for read cursors and private messaging.

Sources checked 2026-10-29: `app/portals/product_returns.py`,
`app/repositories/product_returns.py`, `app/portals/refunds.py`,
`app/repositories/refunds.py`, `app/receipt_ocr.py`, migrations 041–042.

Leader dashboards put role-specific current work before financial detail.
The Sales dashboard shows cumulative original fulfilled sales, all completed
refunds, retained sales, refund types, pending obligations, and collections. It uses
the same `settlement_summary` / `return_settlement_totals` calculation as reports;
cancelled-order refunds do not reduce fulfilled sales, and pending refunds do not
reduce completed totals. Sources checked 2026-10-29: `app/portal_config.py`,
`app/portals/shared.py`, `app/portals/refunds.py`, `app/portals/product_returns.py`,
`app/portals/team_leader.py`, `app/repositories/product_returns.py`,
`app/templates/portals/team_leader.html`, and
`app/templates/portals/team-leader/dashboard.html`. See [[Roles and Access]] and
[[Decisions]].

| Measure | Meaning |
| --- | --- |
| Original sale | Original fulfilled order and line values |
| Confirmed collection | Recorded money received; do not substitute order total |
| Return credit | Approved quantities at original purchase prices |
| Replacement top-up | Separate collection for a more expensive replacement |
| Completed refund | Outgoing transfer that has completed |
| Retained sales report | Original sales less completed refunds under report rules |
| Net collections | Original recorded collections plus top-ups less completed refunds |

A PHP 1,000 sale with PHP 200 completed refund contributes PHP 800 retained
sales. Pending/processing/failed transfers do not reduce retained sales yet.
Submitted report snapshots retain original figures and membership; later refunds
appear as adjustments. Ambiguous legacy records require reconciliation instead
of invented history. See [[Testing and Change Impact]] for the relevant suites.

New [[Sales Activity Reports]] combine personal sales/order actions, return work
and related refund outcomes for the Owner. They select original order dates and
freeze the complete document at submission. The earlier sales-report behavior
described above remains applicable to legacy reports; new activity reports do
not acquire later adjustments. Return handling alone does not claim an original
sale, and actual refund processors remain distinct from the Sales author.
There is no cumulative financial totals panel on the Sales report workspace.
Sources checked 2026-10-29: `app/repositories/activity_reports.py`,
`app/repositories/reporting.py`, `app/report_documents.py`, and migration 037.

Migrations 033–036 connect receipt traceability, report snapshots, financial
alignment, and final Sales authorization. Older workflow descriptions conflict
with newer rules; see [[Questions and Contradictions]].

Sources: [current process](../refund-return-process.md),
[return repository](../../app/repositories/product_returns.py),
[refund integrity](../../app/repositories/refund_integrity.py),
[refund repository](../../app/repositories/refunds.py),
[report settlement calculations](../../app/repositories/report_settlements.py),
[sales report rules](../sales-report-refunds.md),
[Sales authorization migration](../../database/migrations/036_return_sales_authorization.sql).

The 2026-10-30 [[Owner Portal]] places cumulative retained sales, pending obligations,
and net collections on Overview, with definitions and a separate gross reseller
sales chart. Product returns and money refunds share a sidebar destination but keep
separate workspaces and existing actions. Accepted returns without active Supervisor
coverage have an Owner-only fallback filter. Staff handover includes processing
assignments and open post-release follow-ups; actual in-progress refund attempts
require explicit completion/failure/takeover first. Sources:
`app/repositories/owner_insights.py`, `app/repositories/superuser.py`,
`app/repositories/product_returns.py`, `app/templates/portals/owner/dashboard.html`.

## Version-2 replacement requests

New replacement submissions follow [[Replacement Workflow]]: default products and
quantities, direct Supervisor handoff, approval-time stock reservation, complete
Inventory receipt, Inventory release, and Supervisor delivery. Stock shortage
converts the case to a refund after required custody. Sufficient-stock replacements
equal credit and involve no payment. The earlier difference and top-up rules remain
applicable only to historical cases that already recorded those terms.
Sources: `app/repositories/replacement_workflow.py`,
`app/repositories/product_returns.py`, migration 044.

## Return detail grouping (2026-11-02)

The reseller tracking dialog shows one current-status block followed by product
and money facts. Payment actions precede collapsed comparison, returned-item, and
attachment cards. Activity uses short event labels, newest first; full notes remain
available in expandable rows. Supervisor/Owner details also collapse the comparison
and price breakdown, show follow-ups once, and keep conversation/delivery actions
outside reference cards. This changes presentation only. Sources:
`app/templates/portals/reseller/_return_tracking_dialog.html`,
`app/templates/portals/team-leader/_return_case_dialog.html`,
`app/templates/portals/returns/_activity_timeline.html`,
`app/templates/portals/returns/_replacement_actions.html`,
`app/static/css/return_experience.css`. See [[Replacement Workflow]].

## Supervisor case overview (2026-11-02)

Both Supervisor/Owner case-dialog variants expose a compact overview above actions:
current status, reseller, received/requested pack totals, next responsible party,
stock/refund status, credit, replacement value, extra payment, difference refund,
submitted/updated dates, and reason. Long reasons expand; reconciliation flags stay
visible. Current status follows the existing case experience. Missing receipt
metadata overrides it only for an authorized active settlement awaiting extra
payment, never completed/declined/mismatch cases. Stock labels use recorded
preparation, reservation, release, and delivery facts. No workflow or money is
changed. Sources: `app/templates/portals/returns/_supervisor_case_summary.html`,
`app/templates/portals/team-leader/returns.html`,
`app/templates/portals/team-leader/_return_case_dialog.html`,
`app/static/css/return_experience.css`. See [[Replacement Workflow]].


## Inventory dedicated details page (2026-11-03)

Inventory Team Leader uses the same `/portal/team-leader/returns` URL as Sales,
but its stock-only role projection selects a separate template. The queue uses
compact full-width table rows with status, received packs, updated date and a View
case action; three compact cards summarize pack movement. Search covers return,
reseller and delivery references. Manila-date, inventory-status and stock-review
filters retain their role-scoped server query. Case pages show a compact recorded
workflow strip and one current-step summary, responsible role and received-pack
total above Overview, Inventory Actions and Activity History. Product rows separate
batch, expiry, expected/received quantity and inspection, and prominently label all
returned goods non-sellable. Receipt checks compare compact product rows with authorized photo
thumbnails. Preparation and release keep a single task card and the existing forms,
assignment checks, reviewed revision and operation keys.

Activity uses newest-first event rows with actor/date and expandable notes. Receipt,
custody and replacement-batch records remain in closed native disclosures. Overview
keeps reseller and delivery references, returned product details, replacement facts,
and supporting photo previews. Generic action success uses the shared
toast once; errors remain visible inline. POST results retain the active tab and Back
preserves list filters. Financial data and private case conversations remain excluded.
Sources checked 2026-11-03: `app/templates/portals/lifecycle/inventory-returns.html`,
`app/templates/portals/lifecycle/_inventory_case_page.html`,
`app/templates/portals/lifecycle/_inventory_case_actions.html`,
`app/templates/portals/lifecycle/_inventory_case_records.html`,
`app/templates/portals/lifecycle/_inventory_products.html`,
`app/templates/portals/lifecycle/_inventory_case_photos.html`,
`app/templates/portals/returns/_activity_timeline.html`,
`app/static/css/portals/inventory-returns.css`, `app/static/js/return_details.js`.
See [[Roles and Access]] and [[Replacement Workflow]].

## Payout control review (2026-11-03)

[[Return Workflow Gap Review]] records five further outcomes and flags missing
structured beneficiary verification and payout component visibility. Exact-amount,
retry and reconciliation checks passed; the recommendations remain unimplemented.
No real funds were transferred.
