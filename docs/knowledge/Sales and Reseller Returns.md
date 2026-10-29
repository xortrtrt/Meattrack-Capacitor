---
tags: [meattrack, returns, sales, reseller, experience]
verified: 2026-10-29
---
# Sales and reseller returns

Sales' Returns tab and reseller My Returns share a next-step presentation derived
from the original return, current offer, custody, payment evidence, and refund.
This presentation does not replace the business statuses or change Inventory's
complete-match/mismatch workflow. See [[Returns and Financials]],
[[Orders and Inventory]], and [[Roles and Access]].

## Action ownership

The Sales Returns tab defaults to Needs review, with All, Return mismatch,
Payment verification, Waiting on others, and Completed filters. SQL computes
classification/counts within the existing assignment scope before pagination.
Mismatches take precedence; open customer follow-ups, submitted requests, required
Sales confirmations, and reconciliation flags need review. Current-offer payment
proof awaiting verification has its own queue. Rejected requests are terminal
and labeled declined. Standalone money refunds retain their existing workspace.

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

Sales must supply a separate reseller-facing explanation when declining a request
or resolving a receipt mismatch. These explanations appear in customer events and
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
Sales closes the follow-up with a required public resolution summary.

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
