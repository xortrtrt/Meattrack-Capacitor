# Sales, collections, returns, and refunds

## Connected return and refund workflow

The three portal views are linked stages of the existing refund workflow, not
separate financial ledgers. Reseller uses the **My Returns** tab in **Order History**
(`/portal/reseller/history?view=returns`) to track requests. A **Request a return / refund**
button opens an order picker and request form in a modal. Original return URLs redirect
to this tab, preserving order and return links. The separate sidebar entry is removed;
Sales uses **Return Review and Settlement** to approve and confirm proceeding;
Inventory receives and verifies stock. All retain the same return and order IDs.
Existing `order_refunds`, attempts, proofs, references, processor permissions,
and report snapshots remain the monetary source of truth. Pure replacements
create no zero-value refund; only a cash amount owed creates a refund obligation.

New requests require at least one supporting photo (up to three JPG, PNG, or WebP images, 5 MB each). Existing cases remain available without adding photos retroactively.

New return, refund, and replacement requests must be submitted within **5 days
(120 hours)** of the order's recorded fulfillment time. The window closes at the
exact deadline; the UI displays it in Manila time. The picker, available products,
and transactional submission enforce the same rule. Missing or future fulfillment
timestamps require Sales verification. An already submitted request can still be
reviewed, received, and settled afterward; an identical retry returns its original
case without creating a new request. Cancellation refunds keep their existing rules.

After Sales approves a request, Inventory records each physical delivery into
**Non-sellable Returned Goods**. Returned packs never increase sellable batches,
available-to-order balances, or replacement availability. Original batch, line,
location, receipt, receiver, and custody movements remain linked. Disposal is
separate from financial settlement and never erases this history.

Final Inventory verification requires all approved packs and reconciled custody.
It sends **Returned goods complete—Sales confirmation required** to the assigned
active Sales reviewer; missing or inactive assignments notify Owner. Replayed
verification requests do not duplicate receipts, events, or notifications.

Sales must record **Proceed with refund** or **Proceed with replacement** after
verification. The server derives the stored resolution and amount; a submitted
amount cannot override them. Refund obligations remain visible before this
confirmation, but existing refund processors cannot start or complete them.
Supervisor/Owner still record transfers and proof in the existing Refunds page.
Sales confirmation notifies the assigned processor that the linked refund can
continue. Paid cancellation refunds have no return case and keep their existing
direct payment-refund path.

For replacements, both Sales confirmation and customer acceptance are required
before stock reservation. New quotations revoke confirmation and acceptance.
The Inventory and Sales pages distinguish **reserved batches** from **released
batches**, showing batch codes, expiration dates, and quantities. Release checks
current eligibility and financial clearance, deducts those batches, and records
the releasing staff, time, and a direct stock-movement link. Multiple batches
can fulfill a selection. Customers see the actual delivered replacement batches
without warehouse locations, staff audit notes, or company financial totals.

Migration **036** adds Sales authorization metadata and direct movement links for
new releases. Existing completed cases remain terminal without invented approvals
or movement history. Unfinished cases require Sales confirmation before further
settlement. Apply 036 before running this application revision; update both fresh
schema and migration-based installations. The authorization action is
`POST /portal/{role_key}/returns/actions/proceed` with `return_id`, `remarks`, and
an idempotent `operation_key`.

Each refund belongs to its original order. Cancellation refunds have no return
case; product-return refunds and replacement differences have a specific return
case. An original sale, an incoming collection, and an outgoing refund are
different records and must not be counted interchangeably.

| Situation | Required process | Financial result |
| --- | --- | --- |
| Unpaid cancellation | Reseller cancels an eligible unpaid order; release reserved stock. | No collection, sale, or refund. |
| Paid order cancelled before approval | Assigned Sales rejects the pending order, records the reason, and confirms whether money was received. | Confirmed money received creates one full-payment refund obligation. The rejected order never contributes fulfilled sales. |
| Rejected order without money received | Sales records payment not received. Submitted screenshots alone do not establish receipt of money. | No refund obligation or confirmed collection. |
| Refund for fulfilled goods | Reseller selects original fulfilled delivery allocations; Sales approves; Inventory records receipt and separately verifies all goods. | Refund equals the approved quantities at their original purchase prices. |
| Equal-value replacement | Complete return approval and verification; reseller accepts the current quote; Inventory reserves and releases valid stock. | Return credit covers replacement value; no new sale, payment, or refund. |
| Cheaper replacement | Follow the replacement process and release the accepted goods. | One refund obligation for return credit minus replacement total. |
| Dearer replacement | Follow the replacement process; upload proof and have Sales verify the exact extra amount before release. | Record a separate additional collection; no second ordinary sale. |

Resellers cannot self-cancel orders with submitted payment. Approved orders
cannot currently be rejected or cancelled; fulfilled orders use Product Returns.
Requests cover expired and non-expired products traceable to fulfilled reseller
batches. Sales reviews eligibility before approving the return.
Do not treat these restrictions as support for other cancellation/return policies.

## Inventory and Sales Leader responsibilities

Inventory receives a stock-only view of return cases: delivery/return IDs,
products, batches, expiration dates, approved and received packs, receipt and
holding records, physical verification, and replacement reservations/releases.
Its summary counts packs rather than money. Inventory's **Inventory complete**
status means the goods have been verified and any replacement goods released;
Sales may still have an outstanding financial settlement on the same case.

Sales Leader owns the financial view in Product Returns, Sales Refunds, and
Reports: original sales, original purchase prices, return credits, replacement
values, additional collections, completed/pending refunds, and reconciliation.
Supervisors and Owner retain their existing processing access. Both departments
use the same original delivery, return, allocation, receipt, and release IDs.

Inventory's data is filtered on the server as well as in its templates. It
cannot read settlement summaries or payment attachments. Financial case events
and payment references are excluded from its case history; its notifications
contain stock instructions only, including sanitized historical return notices.
Inventory catalog screens omit selling prices, and recipe creation does not
accept price input. Existing Owner pricing controls remain available.

For replacements, Inventory sees **Sales clearance** rather than payment data.
It can reserve eligible stock before Sales verifies an additional collection;
release becomes available only after the accepted selection and server-side
financial checks pass. Physical verification continues to gate Sales settlement.
Removing money from Inventory does not relax either direction of this handoff.

## Financial reconciliation

Migration **035** adds `order_financial_reconciliation`, with one row per order.
It aggregates refunds and returns separately before joining, so multiple items,
receipts, proof records, or failed transfer attempts cannot multiply money.

- **Original sales:** fulfilled order values, including walk-in sales.
- **Original collections:** fulfilled walk-in values; approved/fulfilled reseller
  payments; and payments explicitly confirmed received on rejected orders.
- **Completed refunds:** cancellation refunds + product-return refunds + cheaper
  replacement differences, counted once per completed refund obligation.
- **Net collections:** original collections + verified additional collections −
  all completed refunds.
- **Retained sales:** original fulfilled sales − completed product-return refunds
  − completed replacement differences. Cancellation refunds settle a pre-sale
  collection, so they do not reduce fulfilled sales. Additional replacement
  collections remain separate from ordinary sales reporting.
- **Pending obligations:** pending, processing, and failed refunds. These remain
  owed and do not reduce collections until a successful transfer is recorded.

Sales summaries use the same walk-in/reseller ownership scope as sales reports.
Owner sees all orders; reseller sees their own orders. Supervisor processing
queues still use refund assignment, which is separate from ownership of sales.
Totals are cumulative and use one SQL statement for a consistent snapshot.
Submitted sales report snapshots and later-refund adjustments remain preserved.

## Gates and audit evidence

Refund processing rechecks the original collection ceiling, source order,
approved credit, received quantities, custody trace, final Inventory verifier,
and financial reconciliation flags. Refund amount and source return are locked.
Replacement release additionally checks the current accepted quote total and
exact verified additional payment. A replacement refund also checks released
product quantities against the quote. A case cannot complete with a missing or
mismatched refund obligation.

Only the assigned supervisor or Owner processes refunds. Each completion needs
a successful attempt, reference, actual transfer time, proof, and remarks.
Retries retain failed attempts; replay keys prevent duplicate operations.
An order with both paid and outstanding refunds continues to show the outstanding
status while remaining discoverable in the Refunded filter.

Original-payment and replacement-payment references/proofs are checked across
both collection ledgers. Database transaction locks serialize competing claims.
Existing original-order rules for duplicate proofs/references within a reseller
remain in force. Migration 035 also prevents changing a refund's source return.

Reconciliation flags identify missing cancellation obligations, refunds above
collections, wrong source links, unresolved return records, inconsistent quote
or additional-payment values, and completed refunds without successful transfer
evidence. Flags appear in summaries and refund details and block processing.
They expose existing inconsistencies; they do not invent or erase transactions.

Legacy verified returns with no named final Inventory verifier require Inventory
to recheck the recovered receipts using **Verify returned goods** before their
pending refunds can proceed. Completed legacy cases also expose this action;
recording the actual verifier preserves their completed status, payment history,
and refund amounts. The new verification records the actual operator and remarks
rather than assigning a historical verifier.

## Release verification

Apply migration 035 before using this application revision. Fresh databases use
the matching baseline schema. The migration is additive except for strengthening
the refund source immutability function; it does not rewrite monetary history.
It can be replayed without changing balances. No migration is applied to the
configured application database as part of the automated tests.

`tests/test_order_financial_alignment.py` covers mixed cancellation/return/
replacement/walk-in balances, pending versus completed obligations, missing
refunds, corrupted replacement amounts, source immutability, migration replay,
cross-ledger evidence reuse, and concurrent reference claims. Existing return,
refund, and report suites cover custody, approval, permissions, idempotency,
retries, over-claims, report snapshots, and late adjustments.

## Collection source transparency

Sales return summaries include all accessible confirmed-order collections, even
when those orders have no return cases. Net collections are recorded original
payments plus replacement top-ups minus completed refunds; they are not order
value or profit. A latest-20-order source breakdown identifies each contributing
order and flags recorded payments below approved/fulfilled order values for
review. It never replaces a payment amount with an order total or infers an
actual debt from a possible payment-entry/OCR discrepancy.
