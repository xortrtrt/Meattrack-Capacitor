---
tags: [meattrack, database, operations]
verified: 2026-11-03
---
# Database and operations

## Password change verification (2026-11-03)

Migration `053_password_change_verification.sql` adds account-bound OTP challenges,
hashed verification grants, expiry and attempt counts. It was applied to the
local development database for the profile password workflow. Apply it before
starting this app revision in other environments; production is unchanged.
Fresh databases include the table in `database/schema.sql`. No existing account
passwords are changed by this migration. See [[Roles and Access]] and
[[Architecture]].

## Versioned account policy acknowledgements (2026-11-03)

Migration `052_account_policy_acceptances.sql` adds separate versioned records
for Privacy Notice acknowledgement and Terms acceptance. Apply it before
starting the updated app; otherwise account activation and verified logins that
need policy review will fail when they query the new table. The migration is
present in source; this note does not claim it has been applied locally or in
production. Fresh databases receive the table through `database/schema.sql`.
See [[Roles and Access]] and [[Decisions]].

## Trusted login devices (2026-11-03)

Migration `051_trusted_login_devices.sql` adds account-scoped SHA-256 hashes for
browser device tokens and their fixed trust expiry. Apply it before running the
updated login flow; adding the migration file does not mean it has been applied.
Portal sessions are persisted in `web_sessions` with a 15-minute sliding
inactivity expiry refreshed by navigations or recent user activity, not by
background polling. Remember me changes browser-cookie persistence (up to 30
days), not the server-side inactivity policy. Source: `app/web_sessions.py`,
`app/routers/auth.py`, `app/repositories/accounts.py`, and `app/static/js/core.js`.

## Reseller invitation schema (2026-11-03)

Migration `050_reseller_registration_invitations.sql` adds hashed Owner-issued
invitation records and extends `account_login_otps` with a purpose and per-code
attempt count. Apply the migration before starting code that issues invitations
or verifies registration OTPs. The migration has been added to source; this note
does not claim it has been applied to a database or deployed.

## Local rollback to 049 (2026-11-03)

After the user restored application code to commit `dd21778`, the local database
was explicitly downgraded from 050 to 049 to remove the incompatible GCash refund
guard. A fresh private backup was restored and the exact rollback rehearsed on a
disposable database before application. The six GCash tables were unused apart
from two empty Owner slots, and all new references were null. Only 050's objects
and migration record were removed, without cascading drops. Hash comparisons
preserved all 78 pre-existing public tables (including migration history through
049) and all 52 existing sequences. No transaction reset or production change
occurred. The normal migration runner passed; app/worker restarted and `/health`
returned HTTP 200. No regression suite was restarted. See [[Decisions]].
Sources: `database/migrations/049_scope_replacement_policy_trigger.sql`,
`tools/migrate_database.py`; local operational evidence is in ignored
`outputs/gcash/local-rollback-049.json`. GCash implementation remains stashed,
not active application policy.

## Replacement-first local maintenance completed (2026-11-03)

Migrations 048 and 049 are applied to the verified loopback local development
database. Migration 049 scopes the deferred replacement-first check to policy-2
rows, preserving legacy reconciliation/DDL compatibility; applied 048 was not edited.
The final 1,027-case regression passed before maintenance. App/worker writes were
paused; fresh private backups before and after 049 were actually restored, the
narrow reset rehearsed, and extra preservation/FK postchecks rehearsed on another
restored disposable database before the working reset.

The authorized **transaction-only** reset committed: 37 transaction tables empty,
160 foreign keys valid, protected accounts/catalog/settings/recipes/packages and
unrelated conversations unchanged, batch identities/expiry/quality/physical quantities
and non-sellable custody preserved, no backward identifier sequences. Physical
movements were retained while archived transaction attribution was detached; sold
or released stock was not restored. Scratch schemas outside public were untouched.
App/worker resumed and health returned HTTP 200. Eight synthetic post-maintenance
checks passed in disposable schemas, then public preservation/empty-table checks
and read-only queues for all five roles passed. No deployed reset or upgrade occurred.

`tools/verified_database_backup.py` verifies archive identity, a real restore,
table/sequence contents and reset rehearsal. The transaction-only mode in
`tools/reset_operational_data.py` requires a private verified backup and maintenance
confirmation; it rechecks backup-bound state under table locks. Unknown dependencies
fail closed and validation failures roll back. The maintenance execution also checked
cross-schema dependencies and all public foreign keys before/after reset. Keep backup
archives/manifests private and outside this vault. The historical broad reset below
is a separate mode and must not be substituted for transaction-only maintenance.
Sources: migrations 048–049, `tools/transaction_reset.py`,
`tools/verified_database_backup.py`, `tools/reset_operational_data.py`,
`tests/test_replacement_first_migration_reset.py`.
See [[Decisions]], [[Replacement Workflow]], [[Testing and Change Impact]].

## Transaction history cleared again (2026-11-03)

At the user's request, the transaction-only reset was rehearsed and committed on
the loopback local database using a fresh private backup and the required stopped
app/worker maintenance window. It cleared the three local orders and three
return/replacement cases, including linked proofs, quotes, events, releases,
conversations, and derived transaction artifacts; there were no refund rows.
Accounts, products, settings, physical batches/quantities, inventory and expired
custody movements, unrelated conversations, and migration history were preserved.
Order and return attribution was detached from preserved physical movements and
holdings; no stock was restored. Post-reset order, return, refund, proof, and quote
counts were zero; `/health` returned HTTP 200 and app/worker resumed. No production
database was changed. The private backup remains outside this vault.
Sources: `tools/reset_operational_data.py`, `tools/transaction_reset.py`,
`tools/verified_database_backup.py`. See [[Decisions]] and [[Returns and Financials]].

Migration `047_message_order_context.sql` adds nullable `reseller_messages.order_id`
with an orders foreign key. It does not rewrite historical messages or change
direct-pair uniqueness. The baseline includes the field for fresh test schemas.
On 2026-11-02, the migration runner applied 047 after verifying loopback PostgreSQL
on port 55433 and existing migrations through 046. This is a local development
upgrade, not a production deployment. Apply 047 before serving the new message
reads/writes elsewhere. See [[Background Work and Integrations]].

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
PostgreSQL remains loopback-bound. Sharing the browser UI does not change the
configured base URL used for emailed links. The host computer and Docker must keep
running; visitors use the same local database. Local transactional email uses the
Brevo API configured in `.env`. Checked against Compose and the local network setup
on 2026-11-01. See [[Architecture]] and [[Testing and Change Impact]]. Source:
`compose.yml`.

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


## Component packages (2026-11-04)

Migration `054_component_packages.sql` was applied to the local development
database. It adds component-aware catalog availability, immutable order-package
snapshots, the order-item package link and a guard against standalone package
batch intake. The baseline includes the same objects. The user-authorized local
catalog conversion configured the three reference packages and archived their
standalone balances with correction movements. Component batch balances were
compared before/after and matched. Source: `tools/configure_component_packages.py`
and `app/repositories/inventory/packages.py`.

Other environments need migration 054 followed by explicit reference catalog
configuration using an active Inventory audit actor; missing catalog names,
active standalone package orders/holds and conflicting locked definitions stop
configuration. The maintenance transaction manifest in `tools/transaction_reset.py`
now includes `order_packages`; a disposable-schema rollback check covered it.
No operational reset or production deployment was performed. See
[[Orders and Inventory]], [[Decisions]], and [[Testing and Change Impact]].

## Production demo-data cleanup (2026-11-04)

Production package setup was subsequently checked after release `96bf277`.
Migration 054 was applied, but the three package products were still categorized
as Pork and had no component configuration. Their categories were corrected to
Package after a private backup and checks for active standalone orders, holds and
stock. The user explicitly deferred adding missing Sliced Ham and Triple Spicy
products until the defense. No products or stock were added (18 finished-product
records remain); all three packages stay unavailable with zero configured
components until the full approved contents can be configured. See
[[Orders and Inventory]] and [[Decisions]].

At the user's request, the Contabo production database was reset after the
application and worker were stopped. A private database backup was restored to
an isolated database and the cleanup was rehearsed successfully before applying
the same transaction to production. The live schema included additions beyond
the reset utility's reviewed table manifest, so that utility was not run.

The cleanup retained all 8 accounts, all 18 finished-product definitions, linked
reseller profiles, activation and policy records, invitation/device state,
product-package configuration, product images and migration history (54
migrations). It removed all 7 raw-material records, batches, recipes, inventory
and expiry movements, orders and their payment/refund/return artifacts, plus
other demo operational records. It did not create opening stock or reset identity
sequences. The app and worker resumed; `/health` reported a connected database.
The protected backup remains outside the database and the repository. This is a
production maintenance result, not a code deployment. See [[Decisions]] and
[[Orders and Inventory]]. Sources: `tools/reset_operational_data.py`,
`tools/transaction_reset.py`, `database/schema.sql`, `compose.prod.yml`.
