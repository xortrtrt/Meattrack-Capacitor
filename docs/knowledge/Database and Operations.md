---
tags: [meattrack, database, operations]
verified: 2026-10-28
---
# Database and operations

## Hosting provider

The project owner confirmed on **2026-10-29** that the deployed system uses a
**Contabo VPS**. Earlier Hostinger wording in the README was outdated and has
been corrected. This confirmation identifies the provider; it does not verify
the server configuration, deployed revision, or subscription status.

The VPS expiry/renewal date is not recorded here and requires checking the
Contabo account. Domain registration expiry is a separate date. See
[[Questions and Contradictions]] for the correction's context.

Source: project owner's explicit correction in this chat, 2026-10-29.

## Database lifecycle

PostgreSQL 16 is the persistent business database. `app/database.py` provides
pooled connections and transaction cursors. The baseline is `database/schema.sql`;
numbered SQL migrations upgrade existing databases. At initial note creation,
there are 36 migration files, ending with `036_return_sales_authorization.sql`.
Check the directory and database migration records before an actual deployment.

The migration runner records filenames and SHA-256 checksums and rejects changes
to already applied migrations. Add a new ordered migration rather than editing
deployed history. Verify both fresh baseline and upgrade behavior.

| Data area | Relationship |
| --- | --- |
| Accounts, resellers, inquiries, sessions | [[Roles and Access]] and onboarding |
| Orders, items, batches, immutable movements | [[Orders and Inventory]] |
| Return cases, receipts, holding, refunds, snapshots | [[Returns and Financials]] |
| Forecast runs/results, notification outbox, chat state | [[Background Work and Integrations]] |

## Routine local commands

```powershell
docker compose up -d --build
docker compose logs -f app
docker compose logs -f mail-capture
docker compose exec app python tools/migrate_database.py
.venv\Scripts\python.exe -m pytest -q
```

Development uses app loopback port 8000 and database loopback port 55433.
Production uses `compose.prod.yml`, an application/worker pair, private PostgreSQL,
host Nginx, and HTTPS. Schema readiness must precede compatible app/worker startup.
The Sales authorization flow requires migration 036. The combined
[[Sales Activity Reports]] workspace additionally requires migration 037 before
the corresponding application code is started; it adds immutable document snapshots
and actor-query indexes. Existing applied migration files remain unchanged.

[[Sales and Reseller Returns]] requires migrations 038–039 before its application
version. It adds immutable current-offer snapshots, private/public event separation,
customer follow-ups, and optional existing-chat return references. Stock and refund
ledgers retain their existing rules.

`tools/seed_database.py` recreates the public schema. It is a destructive demo
utility, not a routine migration command. `docker compose down --volumes` deletes
named data volumes. Preserve database and upload backups together and verify a
restore before relying on them. Never infer current production state from the
historical handoff; see [[Questions and Contradictions]].

## Explicit operational reset

`tools/reset_operational_data.py` implements an explicitly authorized permanent
data reset while preserving accounts (including password hashes and assignments),
their linked reseller profiles and activation tokens, finished-product definitions,
and migration history. It clears other operational tables, raw materials, recipes,
old batches, and immutable ledgers. Sessions are cleared, so users must sign in
again. Reseller inquiry links are cleared when the old inquiries are deleted.
This is a maintenance exception to the ordinary immutable-ledger rules in
[[Orders and Inventory]], not an application correction workflow.

Each finished product receives five approved opening batches of **10 packs**,
totaling **50 packs**, with expiry dates 15 days apart starting at the required
`--expiry-date`. Each batch receives a matching opening movement. The dates are
operator-selected starting-stock values, not a product shelf-life policy.

Run without `--apply` for a complete rollback rehearsal. For the actual reset,
stop the application and worker, run the same command with `--apply`, then restart
them. The tool locks all public tables, rejects unreviewed schema changes, keeps
foreign keys and insert validation enabled, restores immutable truncate guards,
and checks retained records and stock totals before committing. It creates no
backup and does not delete uploaded files or existing backups. Source checked
2026-10-29: `tools/reset_operational_data.py`. See [[Decisions]].

Sources: [database guide](../../database/README.md),
[baseline](../../database/schema.sql), [migration runner](../../tools/migrate_database.py),
[local Compose](../../compose.yml), [production Compose](../../compose.prod.yml),
[deployment guide](../../README.md), [Sales authorization migration](../../database/migrations/036_return_sales_authorization.sql).
