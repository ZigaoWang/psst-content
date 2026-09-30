#!/bin/sh
# Gives Claude Code cloud sessions (which can't use SSH) access to the database over HTTPS:
#   - a database login, psst_agent, that can read and write content but can't change or drop tables;
#   - a secret token for the tunnel at https://psst.zigao.wang/tunnel;
#   - the tunnel service.
# Run as root on the VPS from /www/wwwroot/psst/app. Safe to run again: it keeps the existing password and
# token unless you pass --rotate. Prints the settings to paste into the cloud environment.
set -eu
base=/www/wwwroot/psst
secrets="$base/agent.env"
umask 077

if [ ! -s "$secrets" ] || [ "${1:-}" = "--rotate" ]; then
  password=$(openssl rand -hex 24)
  token=$(openssl rand -hex 32)
  printf 'PSST_DB_PASSWORD=%s\nPSST_TUNNEL_TOKEN=%s\n' "$password" "$token" > "$secrets"
fi
password=$(sed -n 's/^PSST_DB_PASSWORD=//p' "$secrets")
token=$(sed -n 's/^PSST_TUNNEL_TOKEN=//p' "$secrets")

# The login. Content tables can be read, added to, and updated; rows can be deleted only from the link
# tables the tools rewrite (a fact's sources and tags, a tag's labels, names). History tables only grow.
sudo -u postgres psql -q -d psst -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'psst_agent') THEN CREATE ROLE psst_agent LOGIN; END IF;
END \$\$;
ALTER ROLE psst_agent WITH LOGIN PASSWORD '$password' NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 12;
GRANT CONNECT, TEMPORARY ON DATABASE psst TO psst_agent;
GRANT USAGE ON SCHEMA psst TO psst_agent;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA psst TO psst_agent;
REVOKE UPDATE ON psst.fact_events, psst.image_events FROM psst_agent;
GRANT DELETE ON psst.fact_sources, psst.fact_tags, psst.tag_labels, psst.tags, psst.place_names,
              psst.admin_area_names TO psst_agent;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA psst TO psst_agent;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA psst TO psst_agent;
GRANT SELECT ON public.psst_schema_migrations TO psst_agent;
ALTER DEFAULT PRIVILEGES FOR ROLE psst IN SCHEMA psst GRANT SELECT, INSERT, UPDATE ON TABLES TO psst_agent;
ALTER DEFAULT PRIVILEGES FOR ROLE psst IN SCHEMA psst GRANT USAGE, SELECT ON SEQUENCES TO psst_agent;
SQL

# The tunnel only knows the token's hash.
hash=$(printf '%s' "$token" | sha256sum | cut -d' ' -f1)
printf 'PSST_TUNNEL_TOKEN_SHA256=%s\n' "$hash" > "$base/tunnel.env"
chown root:psst "$base/tunnel.env"
chmod 640 "$base/tunnel.env"
install -m 644 server/psst-tunnel.service /etc/systemd/system/psst-tunnel.service
systemctl daemon-reload
systemctl enable -q psst-tunnel
systemctl restart psst-tunnel

cat <<OUT
Cloud environment variables (Claude Code on the web, environment settings):

PSST_TUNNEL_URL=wss://psst.zigao.wang/tunnel
PSST_TUNNEL_TOKEN=$token
PSST_DB_USER=psst_agent
PSST_DB_PASSWORD=$password

They're also in $secrets (readable by root only). Run with --rotate to replace them.
OUT
