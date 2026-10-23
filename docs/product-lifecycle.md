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
- Partial pull-out and disposal are supported. Expired incoming goods go directly
  into holding through **Record an already-expired arrival**.

## Reseller return workflow

1. From a fulfilled order, choose **Return / Refund / Replace**, select expired
   batch quantities, choose a resolution, and enter remarks. Photos are optional.
2. Assigned sales staff approve or reject the request. Approval is required before
   inventory receives the goods. An expired item is eligible for review whether
   it expired before delivery or while held by the reseller; approval is manual.
3. Inventory records actual matching receipts and the holding location. Partial
   receipts do not start settlement. Discrepancies block processing until sales
   reviews them; corrected quantities cannot be below quantities already received.
4. Refunds use original purchase prices and enter the existing supervisor refund
   workspace after full verification. Several partial refunds may belong to one
   order. Refund retries preserve their attempt history.
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

Historical allocation backfill uses only complete, unambiguous fulfillment
movements. Inventory staff must document missing batch attribution using
**Legacy orders requiring batch verification** before a legacy claim can settle.

Return summaries show cumulative accessible original sales, confirmed collections,
verified return credit, released replacement value, additional collections,
completed return refunds, and net collections. Existing gross-sales records remain
unchanged. Holding balances are separate from warehouse balances.

## Migration and verification

Run `python tools/migrate_database.py` before starting the updated app and worker.
Migrations 029–031 add the lifecycle tables, indexes, refund links, and operation
keys. The fresh-install schema includes the same changes. Existing warning windows
below 14 days are raised; longer windows are preserved. The first updated worker
scan creates pull-out tasks without claiming physical removal has occurred.

Run the lifecycle integration and browser checks with:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_product_lifecycle.py tests/test_product_lifecycle_browser.py -q
```

Browser checks use Chromium and the actual application against an isolated
PostgreSQL schema. Screenshots are written to `outputs/lifecycle-revisions/`.

Raw-material expiry, supplier returns, and stock-waiting backorders are outside
this revision. There is no automatic return approval or fixed return deadline.
