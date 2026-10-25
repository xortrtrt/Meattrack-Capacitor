# MEATTRACK project handoff

Updated: 2026-10-25

This guide is for a new maintainer joining from another Codex account. It summarizes the product, implemented business rules, repository and deployment status, verification, and safe next steps. Read it with the project README and database guide before changing code or production data.

## Refund traceability revision � 2026-10-26

The current source adds migration **033**, explicit final Inventory verification,
immutable returned-item receipt records, structured refund case history,
reconciliation flags, and a **Sales Refunds** section for assigned sales leaders.
Sales leaders have view-only access to their assigned order refunds and transfer
proof; supervisors and Owner retain financial processing. New completions require
proof, a transfer reference/date, and settlement remarks. Returned goods remain
non-sellable. See [the release and rollback guide](refund-traceability-release.md)
and [the lifecycle guide](product-lifecycle.md) for the revised workflow.

The Git/deployment references in the earlier snapshot below are historical.
Use `git log -1 -- app database` to identify the refund application release and
verify the server SHA before future operational work. Local release evidence and
private backup locations are retained by the project owner; they are not copied
into a fresh clone. Existing customer data and demo assignments must be preserved.

## Current status at a glance

- Project: MEATTRACK, the Batangas Premium website and operations system.
- Git repository: https://github.com/xortrtrt/Meattrack-Capacitor
- Branch: main. At the start of this handoff update, the local checkout matched origin/main.
- Starting repository HEAD before this handoff expansion: 7228cc319e000e914b58f07be2a3d93481e7b715. This expanded handoff is committed as a documentation-only follow-up; run git rev-parse HEAD for the current docs SHA.
- Latest deployed application revision reported by the release record: 6b493d1545fb32f811576dfe757e6699a54b843f. The handoff commit does not change application code, schema, deployment files, or dependencies.
- Live check on 2026-10-25: https://batangaspremium.com returned HTTP 200; /health returned {"status":"ok","database":"connected"}. The health endpoint does not expose a revision, so check the server revision if an exact deployed SHA is needed.
- The latest observed GitHub Actions run for 7228cc3 had quality and production-security checks passing; test and browser-smoke were still running when checked. Check the Actions page for the final run status before treating this documentation commit as fully CI-verified.
- Database baseline plus 33 ordered migrations; latest: database/migrations/033_refund_inventory_traceability.sql.
- A 30-minute authenticated 10-user live acceptance run passed against the application repair release. Details and limits are below.
- Do not commit or share credentials, .env files, database backups, payment evidence, private reports, or activation links.

## What the system does

MEATTRACK is a single FastAPI application with a public Batangas Premium site and role-based business portals. It supports reseller onboarding and ordering, sales review and payment processing, production and batch stock control, expiry handling, product returns, financial settlement, owner reporting, notifications, and live customer support.

The primary project guides are README.md and database/README.md. Deployment configuration is in compose.prod.yml and deploy/nginx/meattrack.conf. This project currently uses FastAPI, server-rendered Jinja2, PostgreSQL 16, and Docker Compose; despite the old repository name, it is not an Android or Capacitor app.

## Roles and portal scope

| Role | Main responsibilities |
| --- | --- |
| Public visitor | Browse products and company information, use the FAQ chatbot, start reseller inquiry, and optionally chat live with an available sales leader. |
| Reseller | Maintain profile, browse products, manage cart, submit orders and payment proof, follow order status, and submit expired-product return/refund/replacement requests. A reseller sees only their own records. |
| Sales team leader | Review assigned reseller inquiries, approve reseller onboarding, manage assigned accounts and prospects, review orders/payment proofs, communicate with resellers, approve/reject return requests, and approve replacement quotes. |
| Sales supervisor | Handle supervisor-scoped order and refund workflows, verify additional replacement payments, and process assigned refund settlements. |
| Inventory team leader | Manage raw and finished inventory, recipes, production batches, expiry monitoring, inventory movements, pull-outs, expired holding stock, returned goods receipt, and disposal. |
| Owner | View operational and financial dashboards, manage catalog prices and accounts, assign resellers to sales leaders, and run reports and demand forecasts. |

Authorization is enforced server-side through capabilities and role-scoped queries. Hiding a button is not the access-control boundary.

## Business lifecycle and decisions

### Production, batches, and inventory

- Finished products enter normal sellable inventory through the Inventory page’s Produce Product workflow. The separate Receive finished-product batch page/action was removed because the business does not receive finished products directly.
- Production records a finished-product batch and, when a recipe exists, deducts its raw materials. A product without a recipe can still be produced without recipe deductions.
- Keep each production batch’s quantity, production/received date, expiration date, and batch code separate. The requested batch naming pattern is ProductName-0001, ProductName-0002, and so on; for example Bacon-0001.
- Production dates and expiration dates belong to the batch. Do not merge batches with different expiry dates.
- FEFO (first-expiring, first-out) is used when fulfilling orders. Fulfillment records which batch supplied each order line.
- Whole finished-product packs are used in orders and batch quantities; raw materials use supported mass/volume units.
- Product details are paginated. Product detail views should expose product data and related stock/batches as implemented in the portal.
- Earlier setup requests cleared sales history, inventory logs, raw-material records, and reset finished-product stock to 100 packs per product across five batches of 20 with varied receipt/production and expiration dates. Later concurrency testing created clearly labeled transactions and stock movements. Treat production data as live; inspect the current database and its audit records rather than assuming the earlier reset state remains true. The code still supports raw-material inventory even though raw-material rows were cleared at that time.

### Expiration, alerts, and pull-out

- Business dates use Asia/Manila. A batch can be sold only when approved and its expiry date is later than today in Manila. Sales stop at 12:00 AM Manila time on the expiration date.
- The near-expiry alert default and minimum are 14 days; longer configured windows are preserved. The critical alert threshold is two days. The worker sends/deduplicates alerts by batch and severity; transaction paths enforce expiry rules even if the worker is down.
- When stock expires, eligibility changes immediately. Expiry does not delete stock or pretend it was physically moved.
- Expired warehouse stock receives a pending pull-out record. Inventory staff confirm the actual quantity, holding location, timestamp, staff member, and required remarks. Partial pull-out is supported.
- Expired items move to a non-sellable holding ledger. Warehouse and holding quantities are reported separately. Disposal is a separate action recording quantity, method, staff, date, and remarks; partial disposal is supported.
- Inventory views cover pending pull-out, expired holding, and disposed stock. Receiving an expired reseller return increases holding stock only; it never restores sellable stock.

### Reseller orders and fulfillment

- A reseller submits an order with payment proof. Sales reviews the order and proof, then approves or rejects it through the order workflow.
- Reservations are included in available-to-order calculations. Competing purchases cannot reserve more than valid stock.
- Fulfillment consumes eligible batches by FEFO and records allocations and inventory movements. Replayed operations must not deduct stock a second time.
- Returned products do not reverse or erase the original sale. Return credits/refunds and replacement values are recorded separately from original sales.

### Product return, refund, and replacement workflow

The module is called Product Returns, not Backorders. Initial scope is expired products, including goods that expired while with a reseller, subject to approval and inventory verification. No automatic approval or fixed claim deadline is configured.

Case progression:
Submitted → Sales approved → Awaiting receipt → Inventory verified → Settlement pending → Completed

A submitted case can be rejected by Sales with required remarks. Approved returns identify the original order line, fulfilled batch/allocation, whole-pack quantity, requested resolution, and required remarks; optional photos may be attached. Active and completed claims cannot exceed the fulfilled quantity of a specific allocation. Inventory records actual receipt and any mismatch. A discrepancy blocks settlement until resolved and resubmitted for Sales approval. Original order fulfillment remains unchanged; case status and settlement appear separately.

Three common outcomes:

1. Successful sale: reseller orders and pays; Sales verifies; Inventory fulfills FEFO; original sale remains recorded.
2. Refund-only return: Sales approves; Inventory receives and verifies expired goods into holding; the system calculates credit from original unit prices; supervisor processes the transfer/refund. Failed transfers retain attempt history and can be retried. Partial refunds can coexist on one order.
3. Same-product replacement: the same approval and receipt steps apply; a same-product, same-quantity replacement has no price difference. Valid stock is reserved and later released by FEFO. If replacement stock is unavailable or expires before release, release is blocked until valid stock is available.

Replacement quote rules:

- Sales may quote the same product or approved substitute products in whole packs.
- Substitute prices are captured from catalog pricing when Sales approves the quote. The reseller sees return credit, replacement value, and any difference, and must accept the quote. A changed quote needs renewed acceptance.
- If replacement value exceeds credit, the reseller owes the difference; require payment evidence and Sales/supervisor verification before release. Example: ₱200 credit toward ₱250 replacement requires verified ₱50 payment.
- If credit exceeds replacement value, the unused difference is refunded through the supervisor refund workflow. Example: ₱200 credit toward ₱150 replacement requires a ₱50 refund.
- Replacement stock is reserved only after receipt verification and quote acceptance. Stock deductions and fulfillment batch allocations happen once at release. A case completes only after release and any required refund are complete.
- These financial flows were simulations during load testing; no real bank transfer was made.

## Other implemented project areas

- Public website: product catalog, company/partnership/privacy/terms content, local product assets, FAQ fallback chatbot, reseller inquiry intake, prospect-to-sales live chat.
- Reseller onboarding: inquiry assignment, Sales review, account creation, single-use activation links, password setup, and account assignment.
- Sales operations: reseller directory, order/payment review, messaging, reporting, prospect queue, availability, and Ably live chat.
- Owner operations: dashboard, date-range reports/charts, product pricing, account administration/assignments, demand forecasting with model selection/backtesting.
- Shared infrastructure: database-backed sessions and notifications, durable notification outbox and worker, audit/activity history, CSRF and database-backed request throttling, optional OTP email, optional Brevo, optional OpenRouter, optional Ably.
- Forecasting: daily per-product demand forecasts with Prophet and baseline methods, backtesting, and production-gap ranking.

Raw-material expiry, supplier returns, and real stock-waiting backorders are outside the current revision scope.

## Architecture and important directories

- app/main.py and app/application.py: FastAPI entry and application setup.
- app/portals, app/routers: role portals and route handlers.
- app/repositories: PostgreSQL queries and transaction logic.
- app/templates, app/static: Jinja pages, styles, scripts, images.
- app/worker.py: durable notification and background work.
- app/business_time.py: Manila business-date rules.
- database/schema.sql: fresh-install baseline.
- database/migrations: ordered schema upgrades; never modify applied migrations.
- tests: unit, integration, PostgreSQL and browser tests.
- tools: migration, seed, import, and operational utilities.
- compose.yml: local app/database/worker/mail-capture environment.
- compose.prod.yml: production services.
- deploy/nginx: reverse proxy configuration.

Python 3.13, FastAPI, Uvicorn, PostgreSQL 16, psycopg2 pooling, Jinja2, and vanilla JavaScript are the current stack. Production uses Docker Compose on an Ubuntu VPS behind Nginx/HTTPS. The application has two Uvicorn workers; the tested configuration used ten DB connections per app worker and three worker-service connections. The VPS PostgreSQL max connection setting observed during acceptance was 100, with three reserved.

## Repository and release status

The public repository is https://github.com/xortrtrt/Meattrack-Capacitor. At the start of this handoff update, main was at 7228cc319e000e914b58f07be2a3d93481e7b715 and matched origin/main. This handoff expansion is a later documentation-only commit. The immediately preceding app/browser commit is 6b493d1. Current main also contains application releases through 39ddfc9 for the lifecycle workflows and fb4d379 for transaction/concurrency repairs. Check GitHub main and Actions before making release decisions.

The live website was checked on 2026-10-25: public home page returned HTTP 200 and /health returned HTTP 200 with database connected. The previous release report records deployed app SHA 6b493d1545fb32f811576dfe757e6699a54b843f. The health URL does not report its version; verify the exact deployed SHA on the VPS when needed. The current handoff commit is documentation-only and does not require an app deployment.

At the time of this edit, GitHub Actions for 7228cc3 reported quality and production-security successful while test and browser-smoke were still in progress. Consult the CI page for their final state. CI includes Ruff, mypy, compilation, full pytest with PostgreSQL, migration/import safety checks, Chromium Playwright tests, and production security-configuration checks.

## 10-user live acceptance evidence

A live 10-session test was run against https://batangaspremium.com on the transaction-repair application revision fb4d379385c2bb7b5060bfc03c0c7f3aa28301bd. It ran 1,802 seconds (30 minutes), used five reseller accounts, three sales leaders, a temporary inventory leader, and a temporary refund supervisor, and recorded 8,565 HTTP requests. The test included a five-minute burst of 20-request reads, mobile/desktop Chromium sessions, concurrent order/fulfillment/return/replacement/refund/pull-out/disposal work, last-stock contention, duplicate submissions, and reconciliation.

Results recorded for that run:

- 0 unexpected HTTP 5xx errors; 0 network errors or timeouts.
- 36 acceptance checks passed, none failed; all 10 browser sessions passed with no JS exceptions or horizontal overflow.
- Ordinary reads: p95 0.748 s; ordinary writes: p95 0.403 s; receipt OCR: p95 8.644 s.
- 98 health samples with 0 failures; sampled peak database connections 14; peak sampled lock waits 0; no deadlocks observed.
- 640 tests passed, 18 skipped; Ruff, mypy, compilation, importer checks, migration fresh/upgrade verification, and added concurrency regressions passed for that release.
- Reconciliation: five paid/fulfilled test orders, FEFO allocations, no duplicate sale deductions, no remaining reservations, returned units in holding rather than sellable inventory, holdings reconciled to movements, refunds/replacements and additional-payment differences matched the test ledger.
- Financial values in that test were explicitly simulated, not real payments.
- This supports the tested 10-user pattern only; it does not prove arbitrary capacity or peak seasonal performance.

The detailed evidence directory was outputs/stress-20261024-031041 in the original local checkout. It is git-ignored and will not be present in a friend’s clone; this summary preserves the important results. A separate public-page load check tried 1, 5, 10, and 25 concurrent clients for short sequential stages, with 246 successful responses. That brief public-only test is not equivalent to 25 active authenticated users.

The acceptance report says the pre-change database backup was made on the VPS and an off-server verified copy plus a Git recovery bundle were retained locally. It also says the five original business-table fingerprints were unchanged; temporary test staff were deactivated and test products hidden. Labeled test orders, returns, financial records, and movements remain as audit evidence. Confirm current accounts and test records in the production owner portal before cleanup; do not delete or reseed business records casually.

## Start and verify locally

Requirements: Docker Desktop/Engine and Docker Compose v2.

1. Clone the repository and read README.md and database/README.md.
2. Ensure no existing local database volume contains data you need to keep.
3. Start the stack with: docker compose up -d --build
4. Visit http://127.0.0.1:8000 and confirm http://127.0.0.1:8000/health.
5. The local stack includes PostgreSQL, app, worker, and mail-capture. View development email/OTP with: docker compose logs -f mail-capture.

For native Python development, use the virtual environment and commands in README. The local PostgreSQL port is 55433. Never use tools/seed_database.py against production; it drops and recreates the public schema.

Useful checks:

    .venv\Scripts\python.exe -m pytest -q
    .venv\Scripts\python.exe -m ruff check app tests tools
    .venv\Scripts\python.exe -m mypy app
    .venv\Scripts\python.exe -m pytest -q -m postgres

PostgreSQL tests require the local Compose database. CI also runs tests/e2e in Chromium.

## Database, deployment, and safety rules

- Fresh installs start with database/schema.sql; upgrades apply numbered migrations via tools/migrate_database.py. Current latest migration is 032.
- Never change a migration already applied to production. Add the next ordered migration and test both a fresh database and an upgrade from the previous schema.
- Migration 002 changes inventory-ledger rules and requires the maintenance process documented in README.md. Run production migrations using the deployment procedure and let the compose migration dependency run before app/worker services.
- Keep production secrets in a private secret store or server .env.production. Never copy the workspace .env, credentials, account manifests, activation links, or payment evidence into Git or chat.
- Database backups and profile-upload archives must correspond to the same time window. Test the backup before relying on it.
- A full database restore overwrites business transactions after the backup. Prefer an app rollback that retains compatible migrations when appropriate. Do not restore the obsolete refund uniqueness constraint.
- Do not run destructive seed/reset/import scripts on production. Verify target host and database before any operational command.
- Payments and refunds in prior demonstration/stress workflows were simulated. Check financial records and provider setup before interpreting them as real transfers.
- A check of /health confirms the app/database path only. Worker, mail delivery, backups, domain/DNS, and actual deployed revision require separate verification.

## Account and access handoff

The user requested five reseller demo accounts and three sales leader demo accounts with generated names/emails and reseller-to-sales leader assignments. The stress run also used a temporary inventory leader and temporary refund supervisor; its report says those temporary staff were deactivated afterward. Credentials and provisioning reports are intentionally excluded from Git. The new maintainer should ask the project owner to securely provide any current demo login or hosting access needed. Do not reset passwords, reissue activation links, or create production accounts without a specific need and recorded approval. Existing account activation/password flow uses one-time links and secure password setup.

## Suggested first steps for the new maintainer

1. Pull main and confirm the repository SHA and GitHub Actions state.
2. Read README.md, database/README.md, and this file.
3. Start a disposable local Compose stack and confirm /health; avoid using production data locally.
4. If production work is requested, verify the exact server release SHA, worker status, recent backup and off-server copy, database migration level, and account/test-record state before changing anything.
5. Ask the owner what feature or issue to continue. No unfinished feature is identified by the repository alone; the next assignment should come from the project owner.

## Suggested prompt for the next Codex

Continue MEATTRACK from https://github.com/xortrtrt/Meattrack-Capacitor. Read docs/HANDOFF.md, README.md, and database/README.md first. This is a FastAPI/Jinja2/PostgreSQL 16 operations website for Batangas Premium with public, reseller, sales leader/supervisor, inventory, and owner portals. The main branch and live site were checked on 2026-10-25; the repo was at 7228cc319e000e914b58f07be2a3d93481e7b715 before this documentation-only expansion, and the recorded deployed app SHA is 6b493d1545fb32f811576dfe757e6699a54b843f. The public site and health endpoint returned HTTP 200 with the database connected. A 30-minute ten-user transaction acceptance run passed on fb4d379; summary and limits are in docs/HANDOFF.md. Verify current GitHub Actions and exact server revision before reporting status or deploying. Never use seed/reset utilities or alter production data without an explicit request and a verified backup. Do not expect credentials or ignored stress reports to be in this clone. Ask the owner which task to take next.

