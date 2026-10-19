#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -f compose.prod.yml || ! -f .env.production ]]; then
  echo "Run this script from the production checkout with .env.production present." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Production checkout has uncommitted changes; deployment stopped." >&2
  exit 1
fi

compose=(docker compose --env-file .env.production -f compose.prod.yml)
services_stopped=0

recover_services() {
  local exit_code=$?
  echo "Deployment failed at line ${BASH_LINENO[0]}." >&2
  if [[ "$services_stopped" -eq 1 ]]; then
    echo "Restarting the previous application containers." >&2
    "${compose[@]}" start app worker || true
  fi
  exit "$exit_code"
}
trap recover_services ERR

echo "--- repository ---"
git status --short --branch
echo "Commit: $(git rev-parse HEAD)"

echo "--- database backup ---"
"${compose[@]}" up -d db
stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$HOME/backups"
backup_file="$backup_dir/meattrack-pre-$stamp.dump"
mkdir -p "$backup_dir"
"${compose[@]}" exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$backup_file"
test -s "$backup_file"
echo "Backup: $backup_file"

echo "--- build release ---"
"${compose[@]}" build migrate app worker

echo "--- migrate and restart ---"
"${compose[@]}" stop app worker
services_stopped=1
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" up -d --no-deps app worker
services_stopped=0

echo "--- service status ---"
"${compose[@]}" ps

echo "--- health check ---"
healthy=0
for _attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health; then
    healthy=1
    break
  fi
  sleep 2
done
test "$healthy" -eq 1

echo
echo "DEPLOYED $(git rev-parse --short HEAD)"
