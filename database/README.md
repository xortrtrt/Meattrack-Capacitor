# MEATTRACK database

MEATTRACK uses standard PostgreSQL in development and production.

## Files

- `schema.sql`: baseline schema for an empty database.
- `migrations/*.sql`: ordered, idempotent changes for existing databases.
- `meattrack_erd.mmd`: Mermaid ER diagram for the implemented data model.
- `tools/migrate_database.py`: baseline and migration runner.
- `tools/seed_database.py`: destructive local demo reset and seed utility.

## Create or upgrade a database

Set `DATABASE_URL`, then run:

```powershell
.venv\Scripts\python.exe tools\migrate_database.py
```

If `public.accounts` is absent, the runner applies `schema.sql`. It then applies
each migration that is not already listed in `schema_migrations`. Its checksum
guard rejects edits to migrations that have already run.

For disposable local demo data only:

```powershell
.venv\Scripts\python.exe tools\seed_database.py
```

The seed utility drops and recreates the `public` schema. Never run it against a
database whose records must be preserved.

## Back up and restore

Create a custom-format backup:

```bash
pg_dump "$DATABASE_URL" --format=custom --file=meattrack.dump
```

Restore into an empty target database:

```bash
pg_restore --dbname "$DATABASE_URL" --clean --if-exists --no-owner meattrack.dump
python tools/migrate_database.py
```

Before a production restore, retain the previous target backup and verify the
source dump with `pg_restore --list meattrack.dump`.

## Migration rules

- Never edit a migration that has already run in production.
- Give each new migration a unique increasing numeric prefix.
- Make migrations transactional and idempotent where PostgreSQL permits it.
- Back up production immediately before schema changes.
- Test both an empty installation and an upgrade from the previous release.

## Data model

- Identity and access: `accounts`, activation tokens, PostgreSQL web sessions,
  security events, activity logs, login/password OTPs, and consent history.
- Department references: `departments`.
- Reseller onboarding: `inquiries`, `resellers`.
- Catalog and inventory: `inventory_items`, `inventory_batches`,
  `product_recipes`, immutable `inventory_movements`, and `alerts`.
- Sales: `orders`, `order_items`, payment proofs, carts, and sales reports.
- Forecasting: `forecast_runs`, `forecast_results`.
- Portal notifications: `notifications`, per-account `notification_recipients`,
  and the durable `notification_outbox` processed by `app.worker`.

Raw stock uses `kg`, `g`, or `ml` to three decimal places. Finished stock,
carts, orders, batches, and sell-through quantities use whole `pack` units.
Cross-table triggers enforce recipe and batch item types and units. Fulfillment
locks FEFO batches and writes every deduction in the same transaction as the
order status update. `inventory_movements` rejects update, delete, and truncate;
opening rows define the audit boundary for databases upgraded by migration 002.
