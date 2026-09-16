# MEATTRACK

MEATTRACK is a FastAPI and Jinja2 application for Batangas Premium's public
website and role-based owner, team-leader, and reseller portals.

## Architecture

- FastAPI with server-rendered Jinja2 templates
- HTML, CSS, and vanilla JavaScript frontend
- PostgreSQL accessed only by the FastAPI backend through `psycopg2`
- Password authentication with optional email OTP confirmation
- Product and branding images served from `app/static/img`
- Docker Compose for local development and VPS deployment

The Compose files pin PostgreSQL 16 to match the existing MEATTRACK data
volume. Upgrade PostgreSQL major versions only through a tested dump/restore.

The development and production environments use the same PostgreSQL engine.
Production does not require a hosted database, object-storage service, or native
mobile wrapper.

## Local development with Docker

Requirements: Docker Desktop with Docker Compose.

1. Build and start the application, PostgreSQL, and local email capture:

   ```powershell
   docker compose up -d --build
   ```

2. Open `http://127.0.0.1:8000`.

The application uses the PostgreSQL database named `MeatTrack Database` by
default. Do not run `tools/seed_database.py` against this database; that tool
is retained only for isolated, disposable test environments.

Login OTP is disabled by default in development. Password-change OTP codes, or
login OTP codes when `LOGIN_OTP_ENABLED=true`, are printed by the
`mail-capture` service:

```powershell
docker compose logs -f mail-capture
```

Useful commands:

```powershell
docker compose logs -f app
docker compose exec app python tools/migrate_database.py
docker compose down
docker compose down --volumes  # also removes the local database
```

## Local development without an app container

Start only PostgreSQL, then run FastAPI in a Python virtual environment:

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

## Database management

`database/schema.sql` is the baseline for a new database. Numbered production
migrations live in `database/migrations` and are applied once by:

```powershell
python tools/migrate_database.py
```

The migration runner creates the baseline only when the `accounts` table does
not exist. Applied migration filenames and checksums are recorded in
`schema_migrations`; changing an applied migration causes a hard failure.
See `database/README.md` for backup, restore, and migration details.

## Testing

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

The test suite does not require a live database because database calls are
isolated or mocked by the relevant tests.

## Health check

`GET /health` checks both FastAPI and its PostgreSQL connection. A healthy
response is:

```json
{"status":"ok","database":"connected"}
```

## Hostinger VPS deployment

The production configuration assumes an Ubuntu VPS with Docker Compose and
host-level Nginx. PostgreSQL is private, and FastAPI is bound only to the VPS
loopback interface.

1. Install the Hostinger Ubuntu Docker template, or install Docker Engine and
   Docker Compose on a clean Ubuntu VPS.
2. Point the domain's DNS records to the VPS.
3. Clone this repository on the VPS.
4. Create a private production environment file:

   ```bash
   touch .env.production
   chmod 600 .env.production
   ```

5. Add unique production secrets. Set `POSTGRES_DB="MeatTrack Database"` and,
   at minimum, configure `POSTGRES_PASSWORD`, `SESSION_SECRET_KEY`,
   `DEFAULT_ACCOUNT_PASSWORD`, and the email delivery settings.
6. Start PostgreSQL, apply the schema and migrations, then start the app:

   ```bash
   docker compose --env-file .env.production -f compose.prod.yml up -d db
   docker compose --env-file .env.production -f compose.prod.yml run --rm app python tools/migrate_database.py
   docker compose --env-file .env.production -f compose.prod.yml up -d --build app
   ```

7. Copy `deploy/nginx/meattrack.conf` to `/etc/nginx/sites-available/meattrack`,
   replace `YOUR_DOMAIN`, enable the site, test and reload Nginx, then let
   Certbot add HTTPS and the HTTP-to-HTTPS redirect:

   ```bash
   sudo ln -s /etc/nginx/sites-available/meattrack /etc/nginx/sites-enabled/meattrack
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d YOUR_DOMAIN -d www.YOUR_DOMAIN --redirect
   ```
8. Allow only SSH, HTTP, and HTTPS through the VPS firewall. Do not expose port
   5432. The Compose file publishes the app only at `127.0.0.1:8000` for Nginx.

### Production backup

Create a local backup directory on the VPS and schedule a daily PostgreSQL
custom-format dump. Copy backups to a second machine or storage provider and
test restoration regularly.

```bash
mkdir -p backups
docker compose --env-file .env.production -f compose.prod.yml exec -T db \
  pg_dump -U meattrack -d "MeatTrack Database" -Fc > backups/meattrack-$(date +%F-%H%M).dump
```

Keep at least one verified backup outside the VPS. A VPS snapshot is useful but
is not a substitute for a database backup.

## Existing-data cutover

If records currently live in another PostgreSQL instance:

1. Put the old application into maintenance mode.
2. Create a final `pg_dump` in custom format and verify that the file is not
   empty.
3. Restore it into the VPS PostgreSQL container with `pg_restore`.
4. Run `python tools/migrate_database.py` against the restored database.
5. Compare row counts for accounts, inventory, orders, sales reports, and logs.
6. Confirm login/OTP, every portal, images, email delivery, chatbot behavior,
   uploads, `/health`, secure cookies, restart recovery, and backup restoration.
7. Switch DNS only after these checks pass. Retain the old database backup until
   the new deployment has completed an agreed observation period.

## Demo accounts

- Owner: `patric.mapa@gmail.com` / `demo123`
- Team Leader: `leader@batangaspremium.test` / `demo1234`
- Reseller: `reseller@lipafresh.test` / `demo1234`

These credentials are for local seeded data only. Production passwords must be
changed before any production seed or account creation.

Set `LOGIN_OTP_ENABLED=true` to restore login OTP locally. Production enables
login OTP by default.

## Optional services

- Brevo delivers login OTP and account emails when its API variables are set.
- OpenRouter powers the chatbot when `OPENROUTER_API_KEY` is set; otherwise the
  chatbot uses its approved local FAQ fallback.
