# Product lifecycle revisions

MEATTRACK tracks finished goods by batch, including the received/production date,
expiration date, and whole-pack balances. All expiry decisions use Asia/Manila.
A batch is blocked starting midnight on its printed expiration date. Availability
checks enforce this rule even when the background worker is stopped.

## Inventory operations

- Create finished stock through **Inventory → Produce product**. Production creates
  a batch and deducts raw materials using its recipe. Use separate batches for
  separate expiration dates; **Product Batches** is the batch-monitoring view.
- **Finished Products** opens product details with batches, reservations, stock
  balances, expiry information, and warehouse movement history. Batches show three
  per page in expiration order; movements show five per page, newest first. Each
  list has its own page controls, and stock totals always include all batches.
- Near-expiry warnings begin at least 14 days beforehand; the existing two-day
  critical threshold remains. The worker refreshes expiry state and alerts.
- **Expired Stock** lists warehouse goods pending physical pull-out, expired
  holding quantities, and disposal history. Expiry blocks sales; staff still
  confirm the physical movement and destination.
- Expired Stock uses four tabs: **Pending pull-out**, **Expired holding**,
  **Disposed**, and **Custody history**. Partial pull-out and disposal are supported.
  Disposal requires quantity and remarks; a separate disposal method is not required.
  Direct expired arrivals are no longer offered; verified reseller returns enter
  holding through **Product Returns**.

## Reseller return workflow

Product Returns shows a full-width list of clickable cases. Selecting a case opens
a dialog with its progress, batches, receipts, review actions, and settlement history.
Close it with the close button, Escape, or the backdrop; focus returns to the case.
Existing case links and action redirects open the same dialog, including validation
messages. Filters and pagination remain in the list.

1. From a fulfilled order, choose **Return / Refund / Replace**, select original delivery
   batch quantities, choose a resolution, and enter remarks. Photos are optional.
2. Assigned sales staff approve or reject the request. Approval is required before
   inventory receives the goods. Requests may cover expired or non-expired products; eligibility approval is
   manual and is based on the return reason and original delivery records.
3. Inventory uses **Record returned items received** to record each actual delivery,
   its quantities, holding location, staff, timestamp, and remarks. Every receipt
   links to the original fulfilled batch, holding entry, and custody movement.
   Partial receipts remain awaiting receipt; a complete receipt moves the case to
   **Awaiting Inventory verification**. Discrepancies require Sales reapproval.
4. Inventory explicitly selects **Verify returned goods** after checking the full
   approved quantity and reconciled custody records. This records the verifier
   and remarks and creates a refund-only obligation atomically. New settlements
   require transfer proof, reference, transfer date/time, and settlement remarks.
   Several partial refunds may belong to one order; retries preserve attempts.
5. Replacement quotes may use the original products or substitutes. Identical
   products/quantities preserve the original credit. Substitutes use catalog
   prices captured at approval. The reseller accepts each quote revision.
6. Inventory reserves valid stock after physical verification and acceptance.
   Additional payment cannot be submitted before reservation. Sales verifies
   proof and the exact difference before inventory releases replacement goods.
   Unused credit becomes a supervisor refund when replacement goods are released.
7. Completion requires replacement release and any refund completion. Expired
   reservations block release and are removed by the next worker scan. After a
   verified additional payment, the quote is locked while valid stock is awaited.

Returned expired goods enter a separate holding ledger. They never restore
sellable batch balances, and the original fulfilled sale is not deducted twice.
Replacement releases are stock movements, not duplicate ordinary sales.

## Permissions and records

Resellers access their own cases. Sales staff review assigned orders; inventory
staff receive, reserve, release, and dispose. Supervisors settle their assigned
refunds and can inspect the linked return. Owners have oversight of all cases.
Remarks, staff identities, timestamps, and custody movements remain recorded.
Operation keys and transaction locks prevent repeated submissions or concurrent
claims from producing duplicate stock/settlement actions.

Sales leaders have a **Sales Refunds** section showing only refunds for their
assigned orders, including rejected paid orders and replacement differences.
They can inspect attempts and transfer evidence and follow the links to receipts
and stock custody. Processing actions and message submission remain restricted
to the existing authorized roles; internal processor notes are excluded from
the sales view. Direct URLs and evidence downloads use the same ownership scope.

Before each processing attempt or completion, settlement rechecks original
payment limits, approval, final verification, original prices, received quantities,
custody balances, and required replacement release. Matching operation replays
return the recorded result; changed details or reused references/proof are rejected.
Disposal remains independent of the refund and never changes original sale totals.
The Product Returns reconciliation filter flags missing links and mismatched
credit, custody, or completion records without rewriting audit history. Reports
show cumulative settlement figures separately from period-specific gross sales.

Historical allocation backfill uses only complete, unambiguous fulfillment
movements. Inventory staff must document missing batch attribution using
**Legacy orders requiring batch verification** before a legacy claim can settle.

Return summaries show cumulative accessible original sales, confirmed collections,
verified return credit, released replacement value, additional collections,
completed return refunds, and net collections. Existing gross-sales records remain
unchanged. Holding balances are separate from warehouse balances.

Leader-to-Owner reports separately show retained sales and completed refunds.
Original submitted figures and source order membership are preserved; later
completed refunds appear as adjustments to the current retained balance.
See [sales report refund separation](sales-report-refunds.md).

## Migration and verification

Run `python tools/migrate_database.py` before starting the updated app and worker.
Migrations 029–031 add the lifecycle tables, indexes, refund links, and operation
keys. The fresh-install schema includes the same changes. Existing warning windows
below 14 days are raised; longer windows are preserved. The first updated worker
scan creates pull-out tasks without claiming physical removal has occurred.

Migration 033 adds explicit Inventory verification, immutable receipt headers and
lines, structured financial events, operation records, and reconciliation checks.
It backfills only receipts proven by existing custody records. Historical
verification dates and completed refunds are preserved without inventing verifier
identities. Incomplete legacy attribution blocks new settlement until reviewed.

Run the lifecycle integration and browser checks with:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_product_lifecycle.py tests/test_product_lifecycle_browser.py -q
```

Browser checks use Chromium and the actual application against an isolated
PostgreSQL schema. Screenshots are written to `outputs/lifecycle-revisions/`.

Raw-material expiry, supplier returns, and stock-waiting backorders are outside
this revision. There is no automatic return approval or fixed return deadline.
