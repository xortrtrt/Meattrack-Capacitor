# MEATTRACK Database Artifacts

This folder contains the simplified PostgreSQL database design used by the current FastAPI prototype.

## Files

- `schema.sql` - PostgreSQL DDL for the portal tables, keys, checks, and useful indexes.
- `meattrack_erd.mmd` - Mermaid ER diagram matching the implemented schema.

## Apply the Schema

Create a PostgreSQL database, then run:

```powershell
psql -d meattrack -f database/schema.sql
```

For local development, the reset-and-seed script applies this schema automatically:

```powershell
.venv\Scripts\python.exe tools\seed_database.py
```

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
