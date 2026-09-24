# MEATTRACK

MEATTRACK is Batangas Premium's web-based operations platform. It combines the
public product website with secure owner, sales team leader, inventory team
leader, and reseller portals in one FastAPI application.

The current release is designed for Docker-based local development and an
Ubuntu VPS deployment. It uses a private PostgreSQL 16 database, local static
assets, Nginx, and optional Brevo and OpenRouter integrations. It does not
require Supabase, Render, Capacitor, or a separate frontend application.

## Current features

### Public website

- Product catalog, company, partnership, privacy, and terms pages
- Batangas Premium support chatbot with a safe local FAQ fallback
- Guided reseller lead collection through the chatbot
- Automatic assignment of reseller inquiries to sales team leaders
- Local product imagery, fonts, icons, and chart assets

### Reseller portal

- Dashboard with account and order summaries
- Searchable product catalog and shopping cart
- Checkout and order-history tracking
- Payment-proof upload for submitted orders
- Profile updates and OTP-confirmed password changes

### Sales team leader portal

- Assigned reseller-inquiry review and approval
- Reseller account creation with emailed temporary credentials
- Payment-proof validation and reseller-order processing
- Sales reports and reseller purchase summaries
- Profile and OTP-confirmed password changes

### Inventory team leader portal

- Raw-material and finished-product inventory
- Batch, expiry, and stock movement tracking
- Product recipes and production recording
- Inventory dashboards, movement analytics, and activity logs
- Role-scoped access separate from the sales team leader portal

### Owner portal

- Executive dashboard and period-based sales charts
- Product pricing management
- Sales reports
- Prophet demand forecasts with Philippine holidays and business events
- Account management and reseller-to-team-leader assignment

## Technology

| Layer | Current implementation |
| --- | --- |
| Backend | Python 3.13, FastAPI, Uvicorn |
| UI | Server-rendered Jinja2, HTML, CSS, vanilla JavaScript |
| Database | PostgreSQL 16 through `psycopg2` connection pooling |
| Authentication | Password login, signed sessions, optional email OTP |
| Forecasting | Prophet and pandas |
| Email | Brevo HTTPS API; local capture service during development |
| Chatbot | Local approved FAQ fallback; optional OpenRouter model |
| Deployment | Docker Compose, Nginx, Certbot, Ubuntu VPS |

## Project structure

```text
app/                    FastAPI routes, data access, templates, and static files
database/schema.sql     Baseline schema for a new PostgreSQL database
database/migrations/    Ordered, checksum-protected production migrations
deploy/nginx/           Reverse-proxy configuration for the VPS
tests/                  Application and repository regression tests
tools/                  Database import, migration, reset, and seed utilities
compose.yml             Local development services
compose.prod.yml        Production database and application services
Dockerfile              Python 3.13 application image
```

## Quick start with Docker

Requirements:

- Docker Desktop or Docker Engine
- Docker Compose v2

Build and start the application, PostgreSQL, and the local email-capture
service:

```powershell
docker compose up -d --build
```

Open <http://127.0.0.1:8000>. The PostgreSQL service is exposed only on
`127.0.0.1:55433`, and the application is exposed only on
`127.0.0.1:8000`.

A fresh Docker volume is initialized from `database/schema.sql`. For an
existing volume, apply any pending migrations:

```powershell
docker compose exec app python tools/migrate_database.py
```

Development login OTP is disabled by default. Password-change OTPs, and login
OTPs when `LOGIN_OTP_ENABLED=true`, are printed by the local capture service:

```powershell
docker compose logs -f mail-capture
```

Other useful commands:

```powershell
docker compose logs -f app
docker compose restart app
docker compose down
docker compose down --volumes  # deletes the local PostgreSQL volume
```

## Run Python locally

Use Docker only for PostgreSQL, then run the application in a virtual
environment:

```powershell
docker compose up -d db
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe tools\migrate_database.py
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The default native-development database URL is:

```text
postgresql://meattrack:meattrack@127.0.0.1:55433/MeatTrack%20Database
```

## Configuration

Local defaults are defined in `compose.yml`. Production values belong in a
private `.env.production` file that must never be committed.

| Variable | Purpose | Production requirement |
| --- | --- | --- |
| `POSTGRES_DB` | Database name; defaults to `MeatTrack Database` | Recommended |
| `POSTGRES_USER` | PostgreSQL role; defaults to `meattrack` | Recommended |
| `POSTGRES_PASSWORD` | PostgreSQL password | Required |
| `DATABASE_URL` | Optional full connection URL override | Optional |
| `DATABASE_POOL_MIN` / `DATABASE_POOL_MAX` | Connection-pool bounds | Optional |
| `SESSION_SECRET_KEY` | Signs browser sessions | Required |
| `LOGIN_OTP_ENABLED` | Enables email OTP after password login | Defaults to `true` in production |
| `CONSENT_VERSION` | Version recorded with accepted login consent | Optional |
| `BREVO_API_KEY` | Brevo transactional-email API key | Required for live email |
| `BREVO_FROM_EMAIL` | Verified sender address | Required for live email |
| `BREVO_FROM_NAME` | Sender display name | Optional |
| `OPENROUTER_API_KEY` | Enables the configured hosted chatbot model | Optional |
| `OPENROUTER_MODEL` | OpenRouter model identifier | Optional |
| `OWNER_PASSWORD` | Initial owner seed password | Required by production Compose |
| `TEAM_LEADER_PASSWORD` | Initial team leader seed password | Required by production Compose |
| `RESELLER_PASSWORD` | Initial reseller seed password | Required by production Compose |
| `DEFAULT_ACCOUNT_PASSWORD` | Fallback for provisioned accounts | Required by production Compose |

Generate unique production passwords and secrets. Never reuse the development
defaults outside a disposable local environment.

## Database lifecycle

`database/schema.sql` is the baseline for empty databases. Numbered SQL files
in `database/migrations/` upgrade existing databases. The migration runner
records each filename and SHA-256 checksum in `schema_migrations` and rejects
changes to migrations that have already run.

```powershell
.venv\Scripts\python.exe tools\migrate_database.py
```

`tools/seed_database.py` drops and recreates the public schema. Use it only for
disposable development or test databases; never run it against production or
any database containing records that must be retained.

Migration `002_strict_inventory.sql` creates immutable opening-balance records
and must be deployed while inventory and order writes are paused. Back up the
database, stop application writes, run the migration, deploy the matching
application build, verify ledger balances, and only then restore writes. Do not
run the previous application build after this migration.

See [`database/README.md`](database/README.md) for migration, backup, restore,
and data-model details.

## Importing existing PostgreSQL data

The repository includes two one-time import helpers for older hosted
PostgreSQL installations:

- `tools/import_supabase_catalog.py` inspects the source catalog.
- `tools/import_supabase_database.py` imports compatible database data.

Treat imports as a controlled cutover: back up both systems, stop writes to the
old application, test the import on a copy, apply migrations, compare row
counts, and verify every portal before changing DNS.

## Tests

Install the development dependencies and run the regression suite:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

The tests mock or isolate database calls, so the normal suite does not require
a live PostgreSQL server. When the local Compose database is available, tests
marked `postgres` also create and remove an isolated schema to verify locking,
rollback, constraints, and ledger immutability. Run that release gate directly
with:

```powershell
.venv\Scripts\python.exe -m pytest -q -m postgres
```

## Health check

`GET /health` verifies both the FastAPI process and its database connection.

Healthy response:

```json
{"status":"ok","database":"connected"}
```

The endpoint returns HTTP `503` when PostgreSQL is unavailable.

## Hostinger VPS deployment

The production layout assumes an Ubuntu VPS with Docker Compose, host-level
Nginx, and HTTPS managed by Certbot. PostgreSQL stays on a private Docker
network, while FastAPI binds only to the VPS loopback interface.

1. Install Docker Engine and Docker Compose on the VPS.
2. Point the domain's DNS records to the VPS.
3. Clone the repository and check out the release branch.
4. Create the private environment file:

   ```bash
   touch .env.production
   chmod 600 .env.production
   ```

5. Add strong values for the required variables listed above.
6. Start PostgreSQL, apply migrations, and build the application:

   ```bash
   docker compose --env-file .env.production -f compose.prod.yml up -d db
   docker compose --env-file .env.production -f compose.prod.yml run --rm app python tools/migrate_database.py
   docker compose --env-file .env.production -f compose.prod.yml up -d --build app
   ```

7. Copy `deploy/nginx/meattrack.conf` to
   `/etc/nginx/sites-available/meattrack`, replace `YOUR_DOMAIN`, enable the
   site, and add HTTPS:

   ```bash
   sudo ln -s /etc/nginx/sites-available/meattrack /etc/nginx/sites-enabled/meattrack
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d YOUR_DOMAIN -d www.YOUR_DOMAIN --redirect
   ```

8. Allow only SSH, HTTP, and HTTPS through the firewall. Do not expose
   PostgreSQL or port 8000 publicly.
9. Confirm `/health`, login and OTP delivery, role permissions, ordering,
   payment proofs, inventory actions, forecasts, email, and backups before
   directing users to the new deployment.

## Production backups

Create regular custom-format PostgreSQL backups, copy them off the VPS, and
test restoration periodically:

```bash
mkdir -p backups
docker compose --env-file .env.production -f compose.prod.yml exec -T db \
  pg_dump -U meattrack -d "MeatTrack Database" -Fc > backups/meattrack-$(date +%F-%H%M).dump
```

A VPS snapshot is useful, but it is not a substitute for a verified database
backup stored on another system.

## Local demo accounts

The disposable seed data uses these development-only credentials:

| Role | Email | Password |
| --- | --- | --- |
| Owner | `patric.mapa@gmail.com` | `demo123` |
| Sales team leader | `leader@batangaspremium.test` | `demo1234` |
| Reseller | `reseller@lipafresh.test` | `demo1234` |

Change all seeded credentials before any shared or production deployment.

## Security notes

- All browser database access goes through FastAPI; PostgreSQL is never exposed
  to the frontend.
- Production session cookies are HTTPS-only and portal sessions expire after
  two hours.
- Passwords and OTPs are stored as salted PBKDF2 hashes.
- Payment-proof downloads require an authenticated portal session.
- Sort options and query filters are server-side allowlisted and parameterized.
- Production secrets belong only in `.env.production` or another private
  secret store.
