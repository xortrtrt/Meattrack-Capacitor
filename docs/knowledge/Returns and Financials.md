---
tags: [meattrack, returns, refunds, reporting]
verified: 2026-10-28
---
# Returns and financials

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
2. Assigned Sales approves or rejects quantities traced to original deliveries.
3. Inventory compares all approved packs, batches, and supporting proof, then chooses
   **Confirm complete receipt** or **Report mismatch to Sales**. New portal receipts
   accept the complete approved request; partial quantities are rejected. A mismatch
   blocks replacement and notifies assigned Sales (Owner fallback for an inactive assignment).
4. A complete match atomically records outstanding goods in **Discarded inventory**,
   verifies custody, and clears the current Sales-approved resolution. The original
   Sales approver remains the authorization source; the actual Inventory reviewer is
   recorded in an `inventory_receipt_complete` event. No new Sales confirmation click
   is required for this path. Historical receipt and verification primitives remain
   available for reconciling older cases without rewriting their records.
   Receipt decisions bind to the reviewed approval revision and quantities; a
   changed approval requires reloading before the decision is accepted.
5. A mismatch must be reviewed by Sales before Inventory can confirm a corrected
   complete request. Received goods stay non-sellable; the fixed holding-location
   label does not itself record physical disposal.
6. For replacement: customer acceptance and cleared Sales approval precede reservation.
   Quote revisions revoke those approvals. Verified extra payment, when required,
   precedes release of eligible reserved batches.
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

Sales team leaders use `/portal/team-leader/refunds` for both money refunds and
product-return review (`?view=returns`). The sidebar has one **Refunds & returns**
entry. Legacy Sales `/returns` links and return notifications open the new tab;
review, confirmation, and additional-payment actions redirect back to that tab.
Sales return-action forms render their session CSRF token in the HTML, including
approval, rejection, and confirmation. Opening or refreshing an action URL with GET
redirects to the return workspace and performs no action. Sources checked 2026-10-29:
`app/templates/portals/team-leader/_return_case_dialog.html`,
`app/portals/product_returns.py`, and `tests/test_sales_refunds_returns_workspace.py`.
The Sales money-refunds tab starts with its status totals and case browser, without
the introductory banner or settlement-progress callout. Product returns use
full-width request rows with responsive stacking. Presentation sources:
`app/templates/portals/refunds.html`, `app/templates/portals/team-leader/returns.html`,
and `app/static/css/portals/team-leader.css` (checked 2026-10-29).
Inventory continues using its quantity/custody workspace. Supervisor and Owner
transfer processing retains its existing permissions and Sales-authorization gate.

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
