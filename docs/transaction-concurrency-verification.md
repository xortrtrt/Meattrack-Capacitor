# Transaction repairs and concurrent-user verification

Migration 032 removes the legacy order-message unique constraint by its columns.
Migration 024's literal constraint name could miss PostgreSQL's generated,
truncated name, preventing refund retries and a second partial refund on an
order. Per-operation `dedupe_key` uniqueness and existing audit history remain.
Do not recreate the obsolete constraint during an application rollback: valid
new messages may now share the old three-column key.

Pull-out confirmation checks its operation reference again after acquiring the
product and batch locks. Concurrent replays therefore succeed without repeating
the stock movement or reporting an insufficient balance caused by the first
submission. Disposal retains its operation check inside the holding row lock.

Refund, return, custody, and session database work runs outside the ASGI event
loop. Both database cursor helpers share bounded connection acquisition for
each pool: wait at most 10 seconds, then return HTTP 503 with `Retry-After: 2`.
The application does not automatically retry stock or financial operations.
Production retains two application workers with up to ten connections each and
three connections for the background worker. Check the actual database limit
and leave capacity for administration before deploying.

## Regression checks

Run `pytest -q tests/test_database_concurrency.py tests/test_transaction_repairs.py`
with PostgreSQL available, then the complete test suite, Ruff, and mypy.
The database tests use isolated schemas. Check both a restored legacy database
and an empty database through the complete migration runner; verify that reruns
are harmless and business records and messages are preserved.

## Live acceptance workload

Use five demo resellers, three assigned sales leaders, a temporary inventory
leader, and a temporary refund supervisor. Keep ten authenticated users active
for 30 minutes, including a five-minute burst of 20 concurrent requests.
Exercise ordinary sales, refund retries, multiple partial refunds on one order,
same-product and substitute replacements, payment differences, pull-outs, and
disposal. Spread financial handoffs across the run, rather than testing only
reads after initial setup. Repeat sensitive submissions and compete for the
last stock. Check ten mobile/desktop Chromium sessions as well.

Pass criteria:

- No unexpected 5xx responses, network timeouts, database deadlocks, ledger
  inconsistencies, duplicate settlements/deductions, or overselling.
- Ordinary read p95 below 2 seconds, ordinary write p95 below 3 seconds, and
  receipt OCR p95 below 15 seconds; report each category separately.
- Sample database connections, lock waits, CPU, memory, and health throughout.
  Stop for any ledger mismatch, three unexpected server errors, or three
  consecutive failed health checks.
- Reconcile batch movements, reservations, holding quantities, refund attempts,
  credits, and collections. Expected validation/permission rejections are
  reported separately from unexpected failures.

Back up production before committing/deploying, verify the archive and an
off-server checksum, and retain the previous Git revision or bundle. Keep
credentials and operational reports outside Git. All live test payments must be
clearly simulated. Hide test products and deactivate temporary staff afterward;
retain labeled transaction/audit evidence, which remains in aggregate reports.
Never restore an old database backup over subsequent real transactions merely
to remove test data. Record measured results separately; this workload verifies
ten concurrent users, not maximum site capacity.
