---
tags: [meattrack, testing]
verified: 2026-10-28
---
# Testing and change impact

Choose verification from the behavior changed and the connections it touches.
Documentation setup does not establish that application tests or production pass.

| Change area | Useful existing coverage | Connected notes |
| --- | --- | --- |
| Auth/roles/session behavior | `test_system_hardening.py`, `test_owner_superuser.py`, `test_inventory_sales_separation.py` | [[Roles and Access]] |
| Stock and concurrent transactions | `test_inventory_postgres.py`, `test_database_concurrency.py`, `test_transaction_repairs.py` | [[Orders and Inventory]] |
| Return eligibility and evidence | `test_return_request_window.py`, `test_return_evidence_required.py` | [[Returns and Financials]] |
| Sales/Inventory handoff | `test_return_workflow_handoff.py`, `test_return_handoff_browser.py` | [[Returns and Financials]] |
| Refund custody and financial alignment | `test_refund_traceability.py`, `test_order_financial_alignment.py`, `test_sales_report_refunds.py` | [[Returns and Financials]], [[Database and Operations]] |
| Combined Sales refunds/returns navigation, review actions, and dashboard totals | `test_sales_refunds_returns_workspace.py`, `test_portal_context.py` | [[Returns and Financials]], [[Roles and Access]] |
| Complete-match/mismatch receipt decisions, fixed discarded holding, and replacement clearance | `test_inventory_receipt_decision.py`, `test_inventory_sales_separation.py`, `test_return_handoff_browser.py` | [[Returns and Financials]], [[Orders and Inventory]] |
| Personal activity reports, immutable documents, CSV/Excel/print and Owner access | `test_activity_reports.py`, `test_activity_report_routes.py`, `test_activity_reports_browser.py` | [[Sales Activity Reports]], [[Roles and Access]] |
| Expiry/lifecycle UI | `test_expired_stock_actions.py`, `test_product_lifecycle_browser.py` | [[Orders and Inventory]] |
| Forecasts/chat/email | `test_forecasting.py`, `test_live_chat_postgres.py`, `test_emailer.py` | [[Background Work and Integrations]] |
| Full portal journeys | `tests/e2e/` | [[Architecture]] |

The filenames in the first rows live under `tests/`. Read test fixtures and
markers before running browser or database suites. PostgreSQL tests use isolated
schemas; ordinary mocked tests cannot prove locking or trigger invariants.

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -q -m postgres
.venv\Scripts\python.exe -m ruff check app tests tools
.venv\Scripts\python.exe -m mypy app
```

CI provisions PostgreSQL and browser dependencies, applies migrations, and checks
quality plus integration/browser behavior. Follow current CI definitions rather
than assuming a local shell has the same services or configuration. Record exact
commands and results when reporting a change.

Sources: [CI](../../.github/workflows/ci.yml), [fixtures](../../tests/conftest.py),
[test configuration](../../pytest.ini), [developer requirements](../../requirements-dev.txt),
[project configuration](../../pyproject.toml).
