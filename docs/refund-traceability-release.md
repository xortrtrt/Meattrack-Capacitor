# Refund and Inventory traceability release

## User workflow

Sales leaders now have **Sales Refunds**, a view of refunds for their assigned
orders. It includes return refunds, replacement price differences, and refunds
for rejected paid orders. Supervisors and Owner retain processing access.

For expired-product returns, use the following sequence:

1. Reseller requests a return against the original fulfilled allocation.
2. Assigned Sales approves the items and quantities.
3. Inventory selects **Record returned items received** for each delivery.
4. After all approved packs arrive, Inventory selects **Verify returned goods**.
5. Refund-only obligations appear in the supervisor workspace. Replacement
   cases continue through accepted quotation, valid stock, and release.
6. The processor records a successful external transfer with proof, reference,
   transfer date/time, and settlement remarks. Failed attempts remain recorded.

Receiving goods and verifying goods are separate decisions. Pending receipts or
discrepancies block settlement. Expired goods remain in holding; their disposal
is recorded independently and does not block a correctly verified refund.

## Data and release checks

Migration 033 is additive except for expanding the allowed return statuses.
It adds receipt records, final verification metadata, operation records, financial
case-event links, and a reconciliation view. It does not replace sales, inventory,
refund attempts, proof, or previous migration records. Reliable old custody
entries are backfilled into receipts without inventing a final verifier.

Release verification includes PostgreSQL concurrency and access tests, migration
replay on a fresh database and a restored production copy, browser checks across
all portal roles, lint/type checks, and a focused ten-session live workflow run.
Detailed measured results are retained in the release report under the original
workspace's ignored outputs directory. Credentials and backups are never shipped
in the public repository.

## Rollback and recovery

The pre-release production application revision is
`6b493d1545fb32f811576dfe757e6699a54b843f`; the pre-release repository main revision
is `2a62b1b0a0f0aa521430f74b192b86ec152ea187` (documentation changes only).

Before a rollback, make a current emergency database/upload backup and enter a
write-maintenance window. Keep migrations 032 and 033, receipt/audit records,
and their uniqueness rules. Do not recreate the obsolete message constraint.
The previous UI does not implement the new final-verification step. Keep return
and refund processing paused until a compatible corrected build is available;
do not convert awaiting-verification cases to settled cases as a shortcut.
Use the existing deployment process to rebuild the selected application revision,
retaining the private production environment and server Compose overrides.

A database restore is a separate recovery decision: it removes transactions
created after the selected backup. Stop application and worker writes, retain
the current backup, restore matching database/upload backups, verify migration
checksums and custody/financial ledgers, and only then restore service.

Demo/test transfers are simulations. Hidden test stock and labeled audit records
must not be mistaken for real bank transfers or customer refund instructions.
