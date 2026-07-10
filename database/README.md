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
- staff data: `departments`, `employees`, `employee_attendance`, `employee_tasks`, `employee_merit_evaluations`;
- reseller onboarding: `inquiries`, `resellers`;
- catalog and inventory: `products`, `product_batches`, `raw_materials`, `raw_material_batches`, `product_recipes`, `alerts`;
- sales flow: `orders`, `order_items`, `sales_reports`;
- forecasting: `forecast_runs`, `forecast_results`.

The inventory model keeps raw materials and product recipes because product production must deduct material stock correctly. Shipment, production-run, production-batch, production-material, and inventory-ledger tables are intentionally omitted; product batch source is stored directly in `product_batches.source_type`.

## Application Responsibilities

The database keeps basic referential integrity and simple status/value checks. The FastAPI application still handles workflow logic such as:

- choosing product batches by FEFO order;
- checking product recipes before production;
- deducting `raw_material_batches.quantity_available` by FEFO when product batches are produced;
- decrementing `product_batches.quantity_available` after fulfilled sales;
- calculating order totals before inserting `orders` and `order_items`;
- creating `alerts` for low stock or near-expiry batches;
- writing `activity_logs` for user actions.
