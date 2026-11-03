---
tags: [meattrack, architecture]
verified: 2026-11-03
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

The invitation-only reseller registration flow joins the Owner accounts portal,
`app/repositories/reseller_invitations.py`, existing login OTP persistence in
`app/repositories/accounts.py`, and `app/routers/reseller_registration.py`.
Migration 050 adds invitation records and purpose/attempt fields to the existing
login OTP table. The public registration page requires a valid invitation token;
the login OTP feature is enabled by default in all environments.

Login OTP verification trusts the current browser for 30 fixed days using an
HttpOnly device cookie and a hashed token in `trusted_login_devices`. Portal
sessions now use a 15-minute sliding inactivity deadline. The optional Remember
me setting preserves the browser cookie across restarts for up to 30 days, but
does not extend the server-side inactivity deadline. Authenticated page navigation
and the `app/static/js/core.js` activity heartbeat refresh the session; background
polling does not. Migration 051 adds the device-trust table. See [[Roles and Access]].

Policy acknowledgements are collected during reseller registration or staff
account activation and recorded per account, policy type, and version in
`account_policy_acceptances` (migration 052). Existing accounts proceed to a
portal session after password and configured OTP verification; login does not
prompt them to accept a newer policy version. Sources: `app/routers/auth.py`,
`app/routers/reseller_registration.py`, `app/repositories/accounts.py`,
`app/repositories/reseller_invitations.py`, and
`app/templates/reseller_register.html`. See [[Roles and Access]].

Unsafe production requests pass token validation and a same-site origin check.
The origin check compares the browser source with the incoming request host and
the configured public `APP_BASE_URL` host, which supports deployments behind the
project's reverse proxy. Reseller registration applies password, Philippine
mobile number, and required business-address rules in both the form and
`app/repositories/reseller_invitations.py`. See [[Roles and Access]].

[[Sales Activity Reports]] use `app/repositories/activity_reports.py` for source
selection and immutable submission, `app/portals/activity_reports.py` for scoped
preview/document/download routes, and `app/report_documents.py` for a common HTML,
CSV and Excel presentation model. New report readers prefer saved documents;
legacy reports retain their earlier settlement behavior (checked 2026-10-29).

Sources: [factory](../../app/application.py), [entry point](../../app/main.py),
[repository facade](../../app/repositories/__init__.py), [database access](../../app/database.py),
[inventory facade](../../app/repositories/inventory/__init__.py).

The [[Owner Portal]] has nine main destinations with contextual tabs for existing
secondary workspaces. `app/repositories/owner_insights.py` supplies business/stock
aggregate reads; existing reconciliation functions retain financial authority.
`app/templates/portals/owner/_workspace_header.html` and Owner-only workspace CSS
share the presentation frame. Separate operations, inquiry, material, alert, and
settings templates replace the former `owner/superuser.html` conditional template.
No schema migration is added by the 2026-10-30 overhaul.

API documentation: `/docs` (Swagger UI), `/redoc`, and `/openapi.json` are
registered by FastAPI. The documentation HTML pages alone allow jsDelivr in
`script-src` and `style-src`, because FastAPI loads their UI assets there; the
normal application policy remains restricted to local scripts/styles. Checked
2026-11-01 against `app/application.py`; regression coverage:
`tests/test_api_docs.py`.

## Shared profile password verification

`app/portals/password_change.py` owns password request/verify/save/cancel routes
for all portal roles. `app/repositories/password_changes.py` enforces database
challenge expiry, attempt and resend limits, hashed single-use grants, and the
credential update. `app/templates/portals/_password_change.html` renders three
stages inside each existing profile card. The shared profile loader derives the
stage from the current account/session and the database, and marks profile pages
`no-store`. Profile forms use the existing field-error adapter in `forms.js`.
Migration 053 adds `account_password_change_challenges`; worker maintenance
removes challenges expired more than 30 days ago. See [[Roles and Access]] and
[[Background Work and Integrations]].
