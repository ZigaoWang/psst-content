#!/bin/sh
# One-time (and safe to re-run) server setup for Psst: directories, the service user, the API service,
# nginx, TLS, and nightly backups. Run as root on the VPS from /www/wwwroot/psst/app.
set -eu
base=/www/wwwroot/psst
mkdir -p "$base/public/content/staging/v2/packs" "$base/public/content/production/v2/packs" \
         "$base/public/content/production/v2/history" "$base/public/privacy" "$base/public/coverage" "$base/backup"
id psst >/dev/null 2>&1 || useradd --system --home "$base" --shell /usr/sbin/nologin psst
chmod 755 "$base" "$base/public"
cp -R server/public/. "$base/public/"

# The API connects with the psst_api role, which can only file reports and demand signals.
. "$base/.env"
printf 'PSST_API_DATABASE_URL=postgresql://psst_api:%s@127.0.0.1:5432/psst\n' "$PSST_API_DB_PASSWORD" > "$base/api.env"
chown root:psst "$base/api.env" && chmod 640 "$base/api.env"

# The nightly backup pushes to GitHub with its own deploy key (see docs/RESTORE.md).
if [ ! -d "$base/backup/psst-db-backup/.git" ]; then
  git clone -q github-psst-backup:ZigaoWang/psst-db-backup.git "$base/backup/psst-db-backup" || \
    (mkdir -p "$base/backup/psst-db-backup" && cd "$base/backup/psst-db-backup" && git init -q -b main && \
     git remote add origin github-psst-backup:ZigaoWang/psst-db-backup.git)
  git -C "$base/backup/psst-db-backup" config user.name "Psst backup"
  git -C "$base/backup/psst-db-backup" config user.email "backup@psst.invalid"
fi
# The coverage map's password is kept, readable by root only, in coverage.password (user name: psst).
if [ ! -f "$base/coverage.password" ]; then
  (umask 077; openssl rand -hex 12 > "$base/coverage.password")
  printf 'psst:%s\n' "$(openssl passwd -apr1 "$(cat "$base/coverage.password")")" > "$base/coverage.htpasswd"
fi

install -m 644 server/psst-api.service /etc/systemd/system/psst-api.service
systemctl daemon-reload
systemctl enable --now psst-api.service
systemctl restart psst-api.service

install -m 755 server/backup.sh /usr/local/bin/psst-backup
install -m 644 server/psst-backup.cron /etc/cron.d/psst-backup

# TLS first over plain HTTP, then the full config.
if [ ! -f /etc/letsencrypt/live/psst/fullchain.pem ]; then
  cat > /etc/nginx/conf.d/psst.conf <<CONF
server {
    listen 80;
    server_name ${PSST_HOSTNAMES:-psst.67-230-170-225.sslip.io};
    location /.well-known/acme-challenge/ { root $base/public; }
}
CONF
  nginx -t && systemctl reload nginx
  domains=""
  for h in ${PSST_HOSTNAMES:-psst.67-230-170-225.sslip.io}; do domains="$domains -d $h"; done
  certbot certonly --webroot -w "$base/public" --cert-name psst --non-interactive --agree-tos \
    --register-unsafely-without-email $domains
fi
install -m 644 server/nginx.conf /etc/nginx/conf.d/psst.conf
nginx -t && systemctl reload nginx
echo "Psst server setup done."
