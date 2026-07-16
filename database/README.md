# MEATTRACK Database Artifacts

This folder contains the simplified PostgreSQL database design used by the current FastAPI prototype.

## Files

- `schema.sql` - PostgreSQL DDL for the portal tables, keys, checks, and useful indexes.
- `meattrack_erd.mmd` - Mermaid ER diagram matching the implemented schema.
- `supabase_data_import.sql` - exported local rows prepared for the hosted import.
- `supabase_security.sql` - enables RLS with no public Data API policies.

## Apply the Schema

Create a PostgreSQL database, then run:

```powershell
psql -d meattrack -f database/schema.sql
```

For local development, the reset-and-seed script applies this schema automatically:

```powershell
.venv\Scripts\python.exe tools\seed_database.py
```

## Migrate to Supabase

Use a new or disposable Supabase project, then copy the **direct** or **Session
pooler** connection string from the project's Connect panel. The Session pooler
on port 5432 is the most compatible choice on an IPv4-only network. Do not use
the transaction pooler on port 6543 for the migration.

```powershell
$env:SUPABASE_DB_URL="postgresql://postgres.PROJECT_REF:URL_ENCODED_PASSWORD@REGION.pooler.supabase.com:5432/postgres"
.venv\Scripts\python.exe tools\migrate_to_supabase.py --reset
```

Alternatively, a note containing the Session pooler connection URL, or the
database password plus project HTTPS URL, can be supplied without placing any
secret in the repository:

```powershell
.venv\Scripts\python.exe tools\migrate_to_supabase.py --check --credentials-file "C:\path\to\supabase-credentials.txt"
.venv\Scripts\python.exe tools\migrate_to_supabase.py --reset --credentials-file "C:\path\to\supabase-credentials.txt"
```

`--reset` is mandatory because the importer replaces the MEATTRACK tables. It
does not drop Supabase's `public` schema, so the platform's schema grants remain
intact. After the import, set the deployed FastAPI service's `DATABASE_URL` to
the same Session pooler URL with `?sslmode=require` and restart it.

The security script enables RLS without client policies. This deliberately
blocks the Supabase anon/authenticated Data API roles; all application access
continues through FastAPI and its server-only database credential.

The full export and data-import SQL files are intentionally ignored by Git
because they contain application records and password hashes. Keep them local
and transfer them only through an approved secure channel.

## Simplified Table Set

The schema keeps the tables used by the current screens:

- identity and access: `accounts`, `activity_logs`;
- department references: `departments`;
- reseller onboarding: `inquiries`, `resellers`;
- catalog and inventory: `inventory_items`, `inventory_batches`, `product_recipes`, `alerts`;
- sales flow: `orders`, `order_items`, `sales_reports`;
- forecasting: `forecast_runs`, `forecast_results`.

The inventory model uses one item catalog for both raw materials and finished products. `inventory_items.item_type` separates `raw_material` rows from `finished_product` rows, while `category` keeps business labels such as Pork, Beef, Chicken, or product family. Raw-material stock is stored directly on `inventory_items.quantity_available`; finished product batches live in `inventory_batches`. Shipment, production-run, production-batch, production-material, raw-material batch, and inventory-ledger tables are intentionally omitted; finished product batch source is stored directly in `inventory_batches.source_type`.

## Application Responsibilities

The database keeps basic referential integrity and simple status/value checks. The FastAPI application still handles workflow logic such as:

- choosing product batches by FEFO order;
- checking product recipes before production;
- deducting raw-material `inventory_items.quantity_available` when product batches are produced;
- decrementing finished-product `inventory_batches.quantity_available` after fulfilled sales;
- calculating order totals before inserting `orders` and `order_items`;
- creating `alerts` for low stock or near-expiry batches;
- writing `activity_logs` for user actions.
