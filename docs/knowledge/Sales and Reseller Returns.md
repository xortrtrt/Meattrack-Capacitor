---
tags: [meattrack, returns, sales, reseller, experience]
verified: 2026-10-30
---
# Sales and reseller returns

Sales' Returns tab and reseller My Returns share a next-step presentation derived
from the original return, current offer, custody, payment evidence, and refund.
This presentation does not replace the business statuses or change Inventory's
complete-match/mismatch workflow. See [[Returns and Financials]],
[[Orders and Inventory]], and [[Roles and Access]].

## Action ownership

The Sales **Returns & tracking** workspace defaults to Needs review. Only initial
assigned requests remain actionable by a regular leader. After acceptance,
Supervisor/Owner owns discrepancies, offer changes, payment verification,
authorization, and follow-up resolution. The assigned leader's SQL queue treats
unfinished accepted work as Waiting on others and shows public progress rather
than processor instructions or internal remarks. Counts and pagination keep the
same assignment boundary. Declined requests remain terminal.

Replacement progress and money progress are independent: release can be complete
while a difference refund is pending. Completed summaries use actual received
and released quantities and recorded completion dates. Before receipt, the offer
labels original goods Approved for return rather than claiming they arrived.

## Offers and public explanations

Each published replacement revision has an immutable snapshot of approved return
lines, credit, offered products, prices and totals. A revised offer shows differences
from the preceding published snapshot and requires current-revision acceptance.
Migration 038 preserves current legacy offers as baselines; unavailable earlier
comparisons are not reconstructed from today's mutable quantities.

The initial reviewer must supply a reseller-facing explanation when declining a
request; Supervisor/Owner must explain resolution of a receipt mismatch. These explanations appear in customer events and
notifications; internal review remarks do not. Historically public rejection
reasons are retained, without publishing other historical internal remarks.

## Existing chat and optional follow-ups

Contact Sales opens the existing assigned Sales conversation with an editable
return-context draft. Opening the link sends nothing. Return references do not
grant access to a case beyond existing ownership checks.

After replacement release, Report an issue sends a contextual message and creates
a tracked Sales follow-up. Further issue messages attach to the single open
follow-up; a new report after resolution can create another. A completed case with
an open issue returns to Needs review without reopening stock or settlement.
Supervisor/Owner closes the follow-up with a required public resolution summary.
The assigned leader may continue the general internal conversation but cannot
resolve the transferred case.

When chat cannot route to an active Sales contact, the issue persists and existing
Sales/Owner notification fallback applies. The UI explicitly reports that chat
delivery is unavailable. Message UUID retries are idempotent and cannot be reused
with different return context or content.

Replacement received is an optional, once-only reseller acknowledgment after
release. It does not resolve an issue, change settlement completion, create a new
sale/refund, or modify Inventory records.

Sources: `app/return_experience.py`, `app/repositories/return_customer.py`,
`app/repositories/product_returns.py`, `app/repositories/return_views.py`,
`app/reseller_messaging.py`, `app/routers/live_chat.py`,
`database/migrations/038_return_customer_experience.sql`. Verification is recorded
in the associated unit, PostgreSQL, and browser tests; this note is not a deployment
record. Migrations 038–039 must precede the corresponding app version.

Migration 041 and the current `product_returns`/`return_customer` repositories were
checked 2026-10-29 for the processor handoff. Existing chat return-context links
still send nothing until the reseller submits a message.

New version-2 replacements use [[Replacement Workflow]] instead of the initial
regular-Sales decision and general-chat contact path described above. They submit
requested products and a signed comparison directly to Supervisor/Owner, open a
case conversation in messaging, and keep additional-payment checkout inside Returns.
Existing version-1 cases and refund-only requests retain this note's prior paths.
Sources: `app/repositories/replacement_workflow.py`,
`app/templates/portals/reseller/_replacement_payment.html`, migration 044.

## Request form interaction (checked 2026-11-02)

Eligible Completed orders expose **Request return**. The dialog groups returned
packs, outcome, and supporting details into three steps. Replacement products use
a searchable dropdown with a selected-pack summary. The primary action reviews
the signed comparison first, then changes to **Request return**; quantity edits
invalidate that review. Compact totals precede expandable line pricing.
The sticky footer reports missing fields and comparison errors and permits retry.
The form owns its loading state through `data-keep-label`, because the shared form
handler otherwise makes a prevented submission unclickable. Eligibility, required
photos, signed-comparison validation, and Supervisor routing remain server-enforced.
See [[Replacement Workflow]] and [[Testing and Change Impact]]. Sources:
`app/templates/portals/reseller/history.html`,
`app/templates/portals/reseller/_return_request_dialog.html`,
`app/static/js/replacement_workflow.js`, `app/static/js/forms.js`,
`app/static/css/product_lifecycle.css`.

## Sales case cards (checked 2026-11-02)

Regular Sales return details show a workflow timeline, request facts,
original/replacement products, financial totals, evidence links, Inventory records,
and activity as visible cards. Version-2 cases show their actual replacement stage
instead of the generic handoff label. Steps use recorded approvals, receipts,
payment, valid reservations, release, delivery, and refund facts. Version-1 stock
reservation still precedes extra payment; version 2 prepares stock after payment.
Delivery and difference refund may both remain outstanding after warehouse release.
Historical records and reconciliation warnings stay available without nested
disclosures. Optional substitution and rejection forms retain their disclosures;
transferred cases retain read-only Sales access and sanitized event notes.
All role action boundaries remain unchanged; the shared timeline below now also serves the other return views.
See [[Replacement Workflow]] and [[Returns and Financials]]. Sources:
`app/templates/portals/team-leader/_sales_case_dialog.html`,
`app/templates/portals/returns/_workflow_timeline.html`,
`app/templates/portals/returns/_case_review.html`,
`app/templates/portals/returns/_activity_timeline.html`,
`app/repositories/product_returns.py`, `app/static/css/return_experience.css`.

## Shared return timeline (checked 2026-11-02)

Reseller, Sales, Owner, and Inventory return details use a connected
milestone timeline instead of the current-progress paragraph. It labels recorded
steps Done, Current, or Pending and shows dates only when recorded. Receipt
submission and funds verification are separate milestones. Mismatches and record
review block later steps; declined cases stop future work. Delivery and difference
refund can be current together after release. Follow-ups remain actionable after
settlement without reopening stock.

The presenter runs before the role allowlists. Inventory receives only stock and
generic Supervisor-clearance milestones, ends at Inventory completion, and receives
no payment, refund, reference, or amount values. Reseller receives public milestone
facts. Current-revision confirmed receipt details are required to complete the
submission milestone. Existing version-1 reservation order and version-2 payment
before preparation are preserved. Sources: `app/return_timeline.py`,
`app/repositories/product_returns.py`, `app/repositories/return_views.py`,
`app/templates/portals/returns/_workflow_timeline.html`,
`app/static/css/return_experience.css`. See [[Replacement Workflow]],
[[Roles and Access]], and [[Testing and Change Impact]].

## Supervisor dedicated details page (checked 2026-11-02)

`/portal/sales-supervisor/returns?case=<id>` now renders a dedicated page; the
case dashboard, its filters, summaries, and navigation remain on the route without
`case`. Back removes detail-only parameters and preserves dashboard filters.
The five-stage overview groups existing milestone facts into Submitted, Supervisor
review, Inventory processing, Settlement, and Completed. Inventory and settlement
can be active together; detailed steps and dates remain under Activity History.

Overview, Review Decision, Inventory & Replacement, Settlement, and Activity History
use keyboard-accessible tabs. Evidence thumbnails open the same authorized image
URLs. Approval/rejection remain the existing forms and required fields, with a
persistent action bar and confirmation before submission. Processed requests show
recorded decisions; permitted revisions require explicitly choosing Revise offer.
Payment metadata stays read-only and verification retains its reviewed-state token.
Alternative selections still use authoritative pricing on save; confirmation does
not present the old financial snapshot as the revised quote. Open conversation uses
the existing case conversation and sends nothing automatically; there is no new
clarification business status. See [[Replacement Workflow]] and [[Roles and Access]].
Sources: `app/templates/portals/returns/_supervisor_case_page.html`,
`app/templates/portals/returns/_case_review.html`, `app/static/js/return_details.js`,
`app/static/css/return_details.css`, `app/return_timeline.py`.


## Reseller dedicated details page (checked 2026-11-02)

`/portal/reseller/history?view=returns&case=<id>` opens a full details page.
The My Returns ledger keeps its expandable order-style cards, filters, and
pagination; Return history removes detail-only query parameters. Creating a new
return still uses the existing order/request dialog.

Overview groups return facts, products, evidence, and financial totals. Replacement
& Delivery shows the current offer, per-line pricing, released batches, and optional
receipt acknowledgment. Payment & Refunds keeps the shared order receipt-recognition
form, signed scan token, reseller confirmation, and existing eligibility gates.
Submitted current-revision receipts show pending funds verification. Attachments
open authorized original images; Activity History contains the detailed public
milestones and sanitized events. Timeline and activity use matching framed cards,
with a narrower timeline column and compact state labels. The action bar stays
above the viewport safe area. The five-stage summary uses precomputed public
workflow facts because private payment metadata is removed by the role projection.

The persistent action bar uses existing acceptance and acknowledgment handlers,
with confirmation and duplicate-submit prevention. Offer acceptance remains bound
to its revision. Both dedicated pages use a shared outlined Open conversation button with chat and
chevron icons, keyboard focus, and hover/pressed feedback. Conversation and issue
reporting destinations remain unchanged.
No staff review, inventory, or funds-verification controls are exposed. See
[[Replacement Workflow]], [[Roles and Access]], and [[Testing and Change Impact]].
Sources: `app/templates/portals/reseller/_return_details_page.html`,
`app/templates/portals/reseller/history.html`,
`app/templates/portals/reseller/returns.html`,
`app/templates/portals/returns/_offer_comparison.html`,
`app/templates/portals/returns/_page_dialogs.html`,
`app/static/js/return_details.js`, `app/static/js/portal.js`,
`app/static/css/return_details.css`, `app/return_timeline.py`.
