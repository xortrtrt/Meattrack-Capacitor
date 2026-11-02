---
tags: [meattrack, database, operations]
verified: 2026-10-30
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
Both Compose definitions make the application and worker depend directly on a
healthy database as well as successful migrations. PostgreSQL has a two-minute
health-check start period and a two-minute shutdown grace period to accommodate
crash recovery and clean checkpoints. Docker Desktop starting existing containers
does not enforce Compose dependency readiness; temporary database connection
failures therefore return HTTP 503 with `Retry-After` through application middleware,
including session loading and saving. Writes are not automatically retried.

If PostgreSQL reports that it is starting up, allow recovery to finish. Start with
`docker compose up -d --wait --wait-timeout 300 db`, then bring up the application
and worker through Compose. Do not repeatedly interrupt recovery or delete the
data volume. Checked on 2026-10-30 against `compose.yml`, `compose.prod.yml`,
`app/application.py`, and `tests/test_database_concurrency.py`. See [[Architecture]]
and [[Testing and Change Impact]].

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

## Portal upgrade: migrations 040–042

Run `python tools/preflight_portal_upgrade.py` before the upgrade. It is read-only
and returns a failure exit code with affected order IDs when pending/approved
reseller payments are insufficient. An Owner must reconcile those records before
migration 040 can succeed; historical fulfilled orders and completed refunds are
not rewritten. Do not replace payment values with order totals to pass preflight.

Pause application and worker writes, apply ordered migrations, then start the
compatible application and worker. Migration 040 removes unpaid holds and records
reservation snapshots/releases. Migration 041 transfers accepted unfinished cases
to Supervisor/Owner and queues participant notifications without rewriting custody
or approval history. Migration 042 adds private message attachments, refund read
cursors, and transfer-recognition metadata/amount validation. Fresh installations
use the matching baseline. Applied older migration files remain unchanged.

The checked-in Nginx request limit is 35 MB to admit a 30 MB message plus multipart
fields; deploy/reload that configuration with the application. Backups must include
private attachment tables. The explicit reset utility's reviewed table list now
includes reservation events, message attachments, and refund read cursors; this
change does not run that destructive utility. See [[Orders and Inventory]],
[[Returns and Financials]], and [[Background Work and Integrations]].

Sources checked 2026-10-29: migrations 040–042, `database/schema.sql`,
`tools/preflight_portal_upgrade.py`, `tools/reset_operational_data.py`,
`deploy/nginx/meattrack.conf`. No production deployment is established by this note.

Local verification on 2026-10-30: the read-only preflight passed, the local Compose
application and worker were stopped, migrations 040–042 were applied with
`tools/migrate_database.py`, and both services resumed successfully. This records
the local development upgrade only; the Contabo deployment and host Nginx reload
remain separate operations. See [[Testing and Change Impact]] for regression
coverage.

Migration 043 adds immutable reseller order-receipt acknowledgements independently
of fulfillment and financial records; fresh schema installations include it. It was
applied to the local database on 2026-10-30 with application/worker writes paused,
then both services resumed. Apply it before code that reads order receipt state.
`tools/reset_operational_data.py` includes the receipt table in its reviewed list;
the reset utility was not run. See [[Orders and Inventory]] and [[Decisions]].

## Replacement upgrade: migration 044

[[Replacement Workflow]] requires migration 044 before compatible app/worker code.
It adds versioned replacement metadata/pricing components, append-only case messages,
and read cursors. Existing returns remain version 1; the new reseller replacement
submission opts into version 2. Baseline/schema and the reset tool's reviewed list
include these additions; the destructive reset was not run. On 2026-10-30 the local
loopback development target was verified, app/worker writes were paused, 044 applied
successfully, and both local services restarted. No production upgrade is established.
Sources: `database/migrations/044_replacement_workflow.sql`, `database/schema.sql`,
`tools/migrate_database.py`, `tools/reset_operational_data.py`.


## Optional local Wi-Fi sharing

`compose.yml` lets the app's published port use `LOCAL_APP_BIND_ADDRESS` from the
ignored local `.env`. The default remains loopback-only. For an explicitly requested
Wi-Fi demo, select the all-interface bind locally, then recreate only the app with
`docker compose up -d --no-deps app`. Visitors use the host computer's current Wi-Fi
IPv4 address and app port; the host can continue using localhost. Revert the local
binding to loopback and recreate the app to end sharing.

Check the active Windows firewall profile and Docker Desktop Backend allowance.
Keep PostgreSQL and local mail-capture ports on loopback. Sharing the browser UI
does not change the configured base URL used for emailed links. The host computer
and Docker must keep running; visitors use the same local database. Checked against
Compose and the local network setup on 2026-11-01. See [[Architecture]] and
[[Testing and Change Impact]]. Source: `compose.yml`.

## Local Owner alert schema, 2026-11-01

Migration 045 (`045_owner_alert_workflow.sql`) was applied locally with
`tools/migrate_database.py`; a repeat run verified current migration checksums.
The app and worker were restarted, and stock-alert reconciliation completed
successfully. Migration 045 is required before running the updated alert routes
and worker. This is local verification, not a production migration record.
See [[Owner Portal]] and [[Testing and Change Impact]].


Migration `046_inventory_tools.sql` (2026-11-01) adds batch archival metadata,
immutable submission/dispatch records, locked package contents, and database-backed
product photos. It preserves existing stock, accounts, and histories. The fresh
baseline includes the same definitions; migration replay is supported. Apply 046
before serving the updated templates/routes. The local Docker development database
was migrated in this session; no production migration/deployment was performed.
Sources: `database/migrations/046_inventory_tools.sql`, `database/schema.sql`.
See [[Orders and Inventory]] and [[Testing and Change Impact]].
