# MEATTRACK project handoff

Updated: 2026-10-25

## Project at a glance

MEATTRACK is Batangas Premium's web-based operations platform. A single FastAPI application serves the public product website and authenticated owner, sales team leader, inventory team leader, and reseller portals. The UI is server-rendered with Jinja2, HTML, CSS, and vanilla JavaScript. PostgreSQL 16 is the system of record.

The repository README is the primary setup and operations guide: [README.md](../README.md). Database lifecycle and recovery guidance is in [database/README.md](../database/README.md).

## Current repository state

- Branch: `main`, tracking `origin/main`.
- HEAD: `6b493d1` — `Refresh browser checks for current owner pages and dialog focus` (2026-10-24).
- At handoff time, the working tree was clean.
- The database has a baseline schema and 32 ordered migrations; the latest is `032_fix_order_message_uniqueness.sql`.
- CI is configured in `.github/workflows/ci.yml` for Ruff, mypy, unit/integration tests with PostgreSQL, Chromium E2E browser smoke tests, an import self-check, and production security configuration checks.
- GitHub Actions CI for this exact commit completed successfully on 2026-10-23; this does not prove a live deployment is healthy. See the transfer brief below.

## Main capabilities

- Public catalog, company and partnership pages, FAQ chatbot, reseller inquiry intake, and optional Ably live prospect chat.
- Reseller catalog, cart, checkout, order history, payment-proof upload, and profile/password management.
- Sales leader inquiry review, reseller activation, payment/order processing, reporting, and prospect live chat.
- Inventory raw materials and finished products, batches/expiry, recipes, production recording, movement history, and analytics.
- Owner dashboard, pricing, reporting, account management, and asynchronous demand forecasts.
- Durable notification outbox and worker; PostgreSQL-backed sessions; optional Brevo email and OpenRouter chatbot integrations.

## Start locally

With Docker Desktop or Docker Engine and Compose v2 installed:

```powershell
docker compose up -d --build
```

Then open `http://127.0.0.1:8000`. The local stack includes PostgreSQL, the app, worker, and email-capture service. To view captured development OTP/email messages:

```powershell
docker compose logs -f mail-capture
```

For Python running outside Docker, start only the database and follow the native setup documented in README. The configured local database port is `55433`.

## Verification commands

Use the repository's documented gates when preparing a change:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check app tests tools
.venv\Scripts\python.exe -m mypy app
```

PostgreSQL-specific tests require the local Compose database:

```powershell
.venv\Scripts\python.exe -m pytest -q -m postgres
```

CI also runs Chromium E2E tests in `tests/e2e` against a fresh PostgreSQL service.

## Important operational cautions

- Never run `tools/seed_database.py` against a database whose data must be retained; it drops and recreates the public schema.
- Do not edit migrations that have already run in production. Add a new increasing migration instead.
- Migration `002_strict_inventory.sql` requires a write maintenance window and matching application deployment. Follow README instructions before applying it to an existing production system.
- Apply migrations before starting the matching application build. Production Compose enforces this dependency; avoid bypassing the migration service.
- Do not commit `.env`, `.env.production`, secrets, backups, or account creation reports. Production credentials belong in a private secret store or private `.env.production`.
- Production requires CSRF protection and database-backed rate limiting. Never expose PostgreSQL or app port 8000 publicly.
- Preserve and verify database and profile-upload backups together when restoring; `docker compose down --volumes` deletes local named volumes.

## Suggested next steps for the next owner

1. Read the README and database lifecycle guide before changing setup, migrations, or deployment.
2. Check `git status --short --branch` and review commits since `6b493d1` before starting new work.
3. Check GitHub Actions for commits newer than `6b493d1`; the exact handoff commit passed CI.
4. Start the app using Docker Compose and confirm `/health` before investigating runtime behavior.
5. Identify the specific requested feature or issue; no outstanding task or incomplete change was specified at handoff time.
6. For production work, schedule migration/write-maintenance steps and back up the database and profile uploads before applying changes.

## Transfer brief (for a new Codex account)

Repository: <https://github.com/xortrtrt/Meattrack-Capacitor>

This is the current codebase for the MEATTRACK capstone, a multi-role meat business operations website. Start by reading this handoff, `README.md`, and `database/README.md`. The goal of the project is to provide a public Batangas Premium website plus owner, sales team leader, inventory team leader, and reseller workflows in one application.

### Hosting and release status as checked on 2026-10-25

- The repository contains a production deployment plan for an Ubuntu VPS described as Hostinger in README. It uses Docker Compose, PostgreSQL 16 on a private network, FastAPI bound to `127.0.0.1:8000`, host-level Nginx, and Certbot HTTPS. See `compose.prod.yml` and `deploy/nginx/meattrack.conf`.
- This checkout does **not** establish that the production server is currently provisioned or running. No production domain is configured in the Nginx file (`YOUR_DOMAIN` remains a placeholder), and no deployment target is defined in GitHub Actions. The actual domain, VPS status, DNS, TLS certificate, database state, and deployed commit must be confirmed with the project owner or hosting account.
- The public GitHub repository is `xortrtrt/Meattrack-Capacitor`; the local checkout was clean on `main` at `6b493d1` (`6b493d1545fb32f811576dfe757e6699a54b843f`).
- GitHub Actions CI for that exact commit completed successfully on 2026-10-23. This verifies the repository's CI run, not the status of any live website. [Workflow run](https://github.com/xortrtrt/Meattrack-Capacitor/actions/runs/40013361560).
- A local `.env` file exists in the original workspace and is not tracked. It will not be included when a friend clones GitHub. Do not paste secrets into this document, GitHub, or ordinary chat. Share required credentials through a secure channel and rotate them if their custody is uncertain.

### Features included in the codebase

| Area | Implemented functionality |
| --- | --- |
| Public site | Product catalog, company/partnership/privacy/terms pages, local static assets, FAQ chatbot fallback, reseller inquiry intake |
| Reseller | Product browsing, cart, checkout, order history, payment-proof upload, profile and password management |
| Sales team leader | Inquiry queue, reseller approval and activation links, payment/order processing, reports, live chat with prospects |
| Inventory team leader | Raw and finished stock, batches and expiry, recipes, production, stock movements, analytics, activity records |
| Owner | Executive dashboard, product pricing, sales reports, asynchronous demand forecasting, account management and assignments |
| Shared services | PostgreSQL-backed sessions, role-scoped authorization, email OTP/activation via optional Brevo, durable notification worker, optional OpenRouter chatbot, optional Ably live chat |
| Release/runtime | Docker Compose local and production stacks, migration runner, Nginx reverse proxy, health endpoint, PostgreSQL backup/restore guidance |

### What the next Codex should do first

1. Clone/open the repository and check current GitHub `main`, CI, issues, and PRs; this handoff reflects the local checkout at the commit listed above.
2. Ask the project owner for the production URL/domain and whether Hostinger/VPS deployment is already active. Verify `/health`, DNS, HTTPS, and deployed revision before reporting a live hosting status.
3. If deployment is active, confirm access to the VPS, private `.env.production`, backup location, PostgreSQL volume, and profile uploads with the owner. Never reset or seed production data.
4. Read the deployment and migration notes in `README.md` before making production changes. Keep production secrets out of Git and never bypass migration ordering.
5. Clarify the immediate feature or handoff task with the owner; the repository state itself does not identify a specific unfinished feature.

### Prompt to give the new Codex

> Continue the MEATTRACK capstone from the repository at https://github.com/xortrtrt/Meattrack-Capacitor. Read `docs/HANDOFF.md`, `README.md`, and `database/README.md` first. The codebase is a FastAPI/Jinja2/PostgreSQL operations platform with public, reseller, sales leader, inventory leader, and owner features. The repository has a Hostinger/Ubuntu VPS deployment configuration, but live server/domain status is unverified; confirm it with me before claiming production is live or changing production. Current handoff commit: `6b493d1545fb32f811576dfe757e6699a54b843f`; GitHub Actions CI for it passed on 2026-10-23. Ask me what task to continue after reviewing the project.

