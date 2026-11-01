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
