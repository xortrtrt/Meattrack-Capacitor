# Portal workflow walkthrough

Execution status: completed and reconciled on October 24, 2026 (Asia/Manila).
All five dedicated demo accounts were signed in through the actual login form.
The local execution report and screenshots are generated under
`outputs/portal-workflows-20261024/README.md` and are excluded from source control.

| Scenario | Order | Return case | Result |
| --- | --- | --- | --- |
| Successful sale | #1 | None | Fulfilled; PHP 2,000 collected. |
| Refund only | #2 | #1 | Completed; two expired packs received; PHP 200 demo refund completed. |
| Same-product replacement | #3 | #2 | Completed; two expired packs received and two fresh packs released. |

Verified totals: PHP 6,000 original sales, PHP 200 completed refunds, PHP 200
replacement value, and PHP 5,800 net collections. Demo stock ends at 18 sellable
packs and four packs in expired holding. The original 18 products, 90 batches,
and eight accounts match their pre-demo snapshots.

This walkthrough uses explicitly labeled demonstration records. Payment and refund
evidence must say **DEMO — NO MONEY TRANSFERRED**. No external payment is made.

## Accounts and responsibilities

| Portal | Demonstration actions |
| --- | --- |
| Reseller | Place orders, submit demo payment evidence, request returns, accept a replacement quote, and check results. |
| Sales team leader | Verify demo payment details, approve and fulfill orders, and review return requests. |
| Inventory team leader | Produce demo batches, verify returned quantities, record holding locations, reserve replacement stock, and confirm release. |
| Sales supervisor | Start the refund case and record its simulated settlement. |
| Owner | Review original sales, return credits, replacement values, completed refunds, and net collections. |

## Demonstration setup

- Use dedicated demo identities if the user chooses that option; do not reset existing passwords.
- Back up the database before creating demonstration records.
- Keep the existing product stock and batch dates unchanged.
- Use a separately labeled demo product priced at PHP 100 per pack.
- Create three orders of 20 packs each: PHP 2,000 per order.
- Provide traceable expired demo allocations for the refund and replacement cases.
  Expiration must be simulated on demo data only and documented in the execution report.
- All actual ordering, approvals, return requests, receipts, and settlement actions
  should be performed through the appropriate portal after signing in.

## Scenario 1: Successful sale

1. Reseller signs in, selects 20 demo packs, and checks out.
2. Confirm that the order has reserved stock and is awaiting payment evidence.
3. Submit uniquely identified demo payment evidence for PHP 2,000.
4. Sales signs in, reviews the order and payment, approves it, then marks it fulfilled.
5. Inventory reviews the stock deduction and actual fulfillment allocations.
6. Reseller verifies the fulfilled order. No return case is opened.
7. Owner verifies PHP 2,000 original sales and PHP 2,000 net collections for this sale.

Expected: 20 packs released once; no holding receipt, replacement, or refund.

## Scenario 2: Refund only

1. Complete another PHP 2,000 order using the successful-sale steps.
2. Use the documented expiration simulation for that demo batch after fulfillment.
3. Reseller opens **Return / Refund / Replace**, selects two fulfilled packs,
   chooses **Refund**, and enters demo remarks.
4. Sales approves the return request with remarks.
5. Inventory verifies both returned packs and records a demo expired holding location.
6. Confirm that two packs entered holding without increasing sellable stock.
7. Confirm that a PHP 200 pending refund was created from the original unit price.
8. Sales supervisor opens the refund workspace and starts processing.
9. Record a clearly labeled simulated transfer method, unique demo reference,
   transfer timestamp, and demo proof where supported; complete the local demo refund.
10. Reseller verifies the completion notification; owner reviews the financial result.

Expected: PHP 2,000 original sales, PHP 200 completed refund, PHP 1,800 net
collections. The original order remains fulfilled and the return case completes.

## Scenario 3: Same-product replacement

1. Complete a third PHP 2,000 order using the successful-sale steps.
2. Use the documented expiration simulation for that demo batch after fulfillment.
3. Reseller requests a **Replacement** for two fulfilled packs and enters demo remarks.
4. Sales approves a quote for two packs of the same product and quantity.
5. Reseller accepts the PHP 200 replacement quote against PHP 200 return credit.
6. Inventory receives and verifies the two expired packs into demo holding.
7. Inventory reserves two valid replacement packs. Confirm that reservations reduce
   available-to-order stock before physical release.
8. Inventory releases the replacements using valid FEFO batches.
9. Reseller checks the completed case; owner checks settlement reporting.

Expected: two expired packs in holding; two fresh packs deducted once; no additional
payment and no cash refund. Net collections stay PHP 2,000, and PHP 200 replacement
value is tracked without adding another ordinary sale.

## Evidence to capture during execution

- Signed-in dashboard for each role, without passwords or session tokens.
- Each order's final fulfilled state and FEFO allocation evidence.
- Sales approval and inventory receipt for each return.
- Supervisor refund processing and completion screens.
- Accepted replacement quote, reservation, and release screens.
- Reseller final return/refund statuses and owner settlement totals.
- Order IDs, return-case IDs, refund ID, demo stock balances, and any limitations.

For three completed scenarios at the stated prices, the combined result should be
PHP 6,000 original sales, PHP 200 completed refunds, PHP 200 replacement value, and
PHP 5,800 net collections. The replacement value is not subtracted again from net collections.
