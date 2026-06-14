# MEATTRACK Database Artifacts

This folder implements the revised PostgreSQL ERD for MEATTRACK.

## Files

- `schema.sql` - PostgreSQL DDL with tables, foreign keys, checks, partial unique indexes, triggers, and reporting views.
- `meattrack_erd.mmd` - Mermaid ER diagram matching the implemented schema.

## Apply the Schema

Create a PostgreSQL database, then run:

```powershell
psql -d meattrack -f database/schema.sql
```

The current machine does not have `psql` available, so this file was statically checked but not executed locally.

## Important Rules Implemented

- `accounts.account_type` supports `owner`, `team_leader`, and `reseller`.
- Regular employees are stored in `employees`; only team leaders get employee-linked accounts.
- Reseller accounts must link to a reseller record.
- Assigned team leaders review and approve reseller inquiries.
- Resellers may be created from an inquiry or entered directly.
- Direct product receiving and production-based product batches are both supported.
- Orders consume actual product batches through `order_batch_allocations`.
- Employee monitoring covers attendance, tasks, merit evaluations, and activity logs.
- AI features are represented by inquiry messages and forecast run/result tables.
- Dashboards and reports should query the base tables and views instead of storing duplicated dashboard data.

## Application Responsibilities

The schema enforces structural integrity, account ownership, and major workflow constraints. The FastAPI application should still handle transactional operations such as:

- choosing batches by FEFO order using `product_batches(product_id, expiry_date, quantity_available)`;
- decrementing `product_batches.quantity_available` and raw material batch quantities;
- recalculating `orders.total_amount` from `order_items`;
- creating `alerts` when stock or expiry thresholds are crossed;
- writing `activity_logs` for sensitive actions.
