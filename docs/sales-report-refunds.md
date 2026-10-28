# Sales report refund separation

For cancellation, return, replacement, and collection reconciliation rules, see
[Refund and return process](refund-return-process.md). Migration 035 supplies
the shared cumulative financial balances and reconciliation flags.

Sales leaders submit **retained sales**, with completed refunds shown in a
separate section. A PHP 1,000 sale with a completed PHP 200 refund contributes
PHP 800 to retained sales. Fully refunded orders contribute zero and are omitted
from the retained-order count. Pending, processing, and failed refunds are not
deducted until the transfer is completed.

Product details retain their original delivery quantities and original line
values; the order's retained amount is calculated separately. A replacement
release does not create another ordinary sale. A cheaper replacement's completed
cash refund reduces retained sales; an additional replacement payment remains a
separate collection and is not ordinary sales revenue in this report.

The report form preview and server calculation use the same assigned fulfilled
orders and Manila business dates. The server recalculates when the report is
submitted and ignores supplied totals. It locks source orders in a consistent
order and records their membership, gross values, and completed refunds.

Owner report details show the original submitted figures, current retained sales,
separate completed refunds, and adjustments completed after the captured report.
The report's original stored figures are never rewritten by later refunds. CSV
exports include the submitted values and separated current/refund amounts.

Migration **034** adds report order/refund snapshots and the current-balance view.
It recovers legacy membership only when the original gross amount and order count
match reliable source records. An unmatched legacy report displays **Requires
reconciliation**, rather than presenting an unverified submitted value as retained
sales. Reseller sell-through report records retain their existing separate meaning.

Before deployment, back up the database and uploads and retain an off-server copy
and Git recovery snapshot. Test both a fresh installation and an upgrade.
For an application rollback, keep migration 034 and its snapshot records. The
previous application cannot show current late-refund adjustments, so pause report
submission/review until a compatible build is restored. Never rewrite submitted
figures or delete snapshots to make an old UI appear current.
