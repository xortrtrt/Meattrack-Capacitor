---
tags: [meattrack, decisions]
verified: 2026-10-28
---
# Decisions

## 2026-10-29: Explicit reset with staggered opening stock

The user requested clearing database data while retaining accounts and finished
products, then setting 50 starting packs per product, split into 10-pack batches
with distinct expiries. Preserve linked reseller profiles and activation tokens
to keep account access intact, as well as schema migration history. Clear the old
operational records and stock ledgers in a single maintenance transaction, then
create five opening batches and matching movements per product. The user delegated
expiry selection; use 30, 45, 60, 75, and 90 days from the reset's Manila date.
These dates are reset inputs, not verified shelf-life rules. The reusable tool
requires an explicit first expiry and defaults to rollback. See
[[Database and Operations]], [[Orders and Inventory]], and
`tools/reset_operational_data.py`.

These are existing constraints observed in source/docs, plus the knowledge-vault
setup decision. This log does not invent the original authors' intent.

| Decision | Reason or observed constraint | Connection and source |
| --- | --- | --- |
| Keep a single FastAPI/Jinja2 app | Current implementation shares public and portal infrastructure | [[Architecture]]; `app/application.py`, `README.md` |
| Preserve inventory movement history | Auditability and traceable batch deductions are enforced | [[Orders and Inventory]]; `database/README.md` |
| Show historical whole-product balances in Inventory Logs (2026-10-29) | User found batch balances unclear beside product stock. Aggregate stock deltas before filtering and pagination, exclude non-sellable return receipts, and preserve stored batch snapshots for Product Details. | [[Orders and Inventory]]; `app/repositories/inventory/queries.py`, `app/templates/portals/team-leader/logs.html`, `tests/test_inventory_log_balances.py` |
| Keep returned goods non-sellable | Returned custody must not inflate available inventory | [[Returns and Financials]]; `docs/refund-return-process.md` |
| Separate customer follow-ups from settled return work (2026-10-29) | User approved a Returns-only Sales queue, existing Sales chat, optional receipt acknowledgment, tracked replacement issues, and required public rejection/mismatch explanations. Follow-ups never reopen Inventory or settlement. | [[Sales and Reseller Returns]]; migration 038, `app/return_experience.py`, `app/repositories/return_customer.py` |
| Assign pull-out destination automatically (2026-10-29) | User removed the holding-location form field. New expired-stock portal pull-outs use Expired holding; historical locations remain intact. | [[Orders and Inventory]]; `app/portals/product_returns.py`, `tests/test_expired_stock_location.py` |
| Show the first Pending pull-out transition in Inventory Logs (2026-10-29) | User requested visibility when stock expires. Read the existing unique queue record as a zero-delta status event, retain its original timestamp and quantities, and deduct stock only at physical pull-out. | [[Orders and Inventory]], [[Background Work and Integrations]]; `app/repositories/inventory/queries.py`, `tests/test_pending_pullout_logs.py` |
| Preserve Sales approval as the settlement authority | Migration 036 records authorization. The 2026-10-29 receipt decision below clears a complete match under the existing Sales approval; legacy reconciliation can retain the explicit confirmation action. | [[Returns and Financials]]; migration 036 |
| Preserve submitted report snapshots | Later refunds adjust current retained balances without rewriting original submissions | [[Returns and Financials]]; migration 034 and `docs/sales-report-refunds.md` |
| Check migration checksums | Applied schema history must remain identifiable | [[Database and Operations]]; `tools/migrate_database.py` |
| Report personal activity by original order dates (2026-10-29) | User selected personal recorded actions rather than assignment, and original order dates rather than action dates; return review does not claim a sale. | [[Sales Activity Reports]]; `app/repositories/activity_reports.py` |
| Freeze combined activity reports at submission (2026-10-29) | User requires the Owner, CSV, Excel and print to see the same document; later outcomes require regeneration. Legacy sales reports retain their existing adjustment rules. | [[Sales Activity Reports]], [[Returns and Financials]]; migration 037, `app/report_documents.py` |
| Store project knowledge as linked local Markdown (2026-10-28) | User requested Obsidian context; agents can read the same files without a plugin or running GUI | [[Start Here]]; `AGENTS.md`, `.obsidian/` |
| Consolidate Sales return review into Refunds (2026-10-29) | User requested one workflow for money refunds and product returns; reuse existing review, custody, authorization, and transfer records rather than adding another ledger. Dashboard totals reuse the report settlement calculation. | [[Returns and Financials]], [[Roles and Access]]; `app/portals/refunds.py`, `app/portals/product_returns.py`, `app/portals/team_leader.py` |
| Use a complete-match or mismatch Inventory receipt decision (2026-10-29) | User rejected partial-receipt entry and a second verification step. A complete proof/quantity/batch match records fixed Discarded inventory custody, verifies, and clears the existing Sales-approved resolution atomically. A mismatch notifies Sales and blocks release. Reseller acceptance, extra-payment checks, and authorized transfer processing remain enforced. | [[Returns and Financials]], [[Roles and Access]]; `app/repositories/product_returns.py`, `app/portals/product_returns.py`, `app/templates/portals/lifecycle/_inventory_return_dialog.html` |

Add new entries only when a task establishes a consequential decision. Include
the date, rationale, source/evidence, and affected notes. Do not infer deployment
status or business approval from the existence of a note.
