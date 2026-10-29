---
tags: [meattrack, architecture]
verified: 2026-10-28
---
# Architecture

`app/main.py` exports `app = create_app()`. The factory in `app/application.py`
registers public/auth/chatbot/live-chat routers and owner, team-leader, reseller,
refund, return, and shared portal routers. It also configures sessions, CSRF,
security headers, static files, templates, and portal section loaders.

The main request path is browser → router/portal action → domain repository →
pooled PostgreSQL connection. Templates and local scripts present the result.
Database-backed background work runs separately in `app/worker.py`.

| Layer | Source | Relationship |
| --- | --- | --- |
| Application assembly | `app/application.py` | Wires routers and section loaders |
| Public/auth/API endpoints | `app/routers/` | Uses shared web/security and domain data |
| Portal workflows | `app/portals/` | Builds contexts and handles role-scoped actions |
| Access control | `app/capabilities.py`, `app/portal_dependencies.py` | See [[Roles and Access]] |
| Domain persistence | `app/repositories/` | See [[Orders and Inventory]] and [[Returns and Financials]] |
| Database access | `app/database.py` | Pooled connections and transaction cursors |
| Presentation | `app/templates/`, `app/static/` | Jinja2, CSS, vanilla JavaScript |
| Worker | `app/worker.py` | See [[Background Work and Integrations]] |

`app/repositories/__init__.py` is a compatibility facade that re-exports domain
functions. A call imported as `from app import repositories as data` may be
implemented in a domain module. Inventory itself is a package with queries,
products, raw materials, allocation, and movement modules. Follow the actual
definition before editing a facade or shared helper.

Changes crossing a portal, repository, and migration need coordinated
verification; use [[Testing and Change Impact]]. Schema/deployment ordering is
covered by [[Database and Operations]].

[[Sales Activity Reports]] use `app/repositories/activity_reports.py` for source
selection and immutable submission, `app/portals/activity_reports.py` for scoped
preview/document/download routes, and `app/report_documents.py` for a common HTML,
CSV and Excel presentation model. New report readers prefer saved documents;
legacy reports retain their earlier settlement behavior (checked 2026-10-29).

Sources: [factory](../../app/application.py), [entry point](../../app/main.py),
[repository facade](../../app/repositories/__init__.py), [database access](../../app/database.py),
[inventory facade](../../app/repositories/inventory/__init__.py).
