#!/bin/sh
# One-time (and safe to re-run) database setup for Psst: PostgreSQL 14 with PostGIS, the two roles, the
# database, and its extensions. Run as root on the VPS from /www/wwwroot/psst/app, before setup.sh.
# Passwords are generated once into /www/wwwroot/psst/.env (root only) and reused after that.
set -eu
base=/www/wwwroot/psst
mkdir -p "$base"

if ! command -v psql >/dev/null 2>&1 || ! dpkg -s postgresql-14-postgis-3 >/dev/null 2>&1; then
  apt-get update -q && apt-get install -y -q postgresql-14 postgresql-14-postgis-3
fi

if [ ! -f "$base/.env" ]; then
  owner=$(openssl rand -hex 16)
  api=$(openssl rand -hex 16)
  (umask 077; printf 'PSST_DB_PASSWORD=%s\nPSST_API_DB_PASSWORD=%s\nPSST_DATABASE_URL=postgresql://psst:%s@127.0.0.1:5432/psst\n' \
    "$owner" "$api" "$owner" > "$base/.env")
fi
. "$base/.env"

cd /tmp
psql_as_postgres() { sudo -u postgres psql -q -v ON_ERROR_STOP=1 "$@"; }
psql_as_postgres -tc "SELECT 1 FROM pg_roles WHERE rolname = 'psst'" | grep -q 1 || \
  psql_as_postgres -c "CREATE ROLE psst LOGIN PASSWORD '$PSST_DB_PASSWORD'"
psql_as_postgres -tc "SELECT 1 FROM pg_roles WHERE rolname = 'psst_api'" | grep -q 1 || \
  psql_as_postgres -c "CREATE ROLE psst_api LOGIN PASSWORD '$PSST_API_DB_PASSWORD'"
psql_as_postgres -tc "SELECT 1 FROM pg_database WHERE datname = 'psst'" | grep -q 1 || \
  psql_as_postgres -c "CREATE DATABASE psst OWNER psst ENCODING 'UTF8' TEMPLATE template0"
psql_as_postgres -d psst -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pg_trgm;
                             CREATE EXTENSION IF NOT EXISTS unaccent;
                             REVOKE ALL ON DATABASE psst FROM PUBLIC; GRANT CONNECT ON DATABASE psst TO psst, psst_api;"

cd "$base/app"
PSST_CONFIG="$base/.env" /root/.local/bin/uv run --no-dev psst db migrate
echo "Database ready."
