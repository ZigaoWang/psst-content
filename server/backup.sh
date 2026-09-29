#!/bin/sh
# Nightly Psst backup, run by cron as root (see psst-backup.cron):
#  1. a pg_dump of the whole database, kept 14 days on this server;
#  2. a sorted plain-text export committed and pushed to the private GitHub repo psst-db-backup.
set -eu
base=/www/wwwroot/psst/backup
stamp=$(date -u +%Y-%m-%dT%H%MZ)
mkdir -p "$base/dumps"
cd /tmp
sudo -u postgres pg_dump -Fc psst > "$base/dumps/psst-$stamp.dump.tmp"
mv "$base/dumps/psst-$stamp.dump.tmp" "$base/dumps/psst-$stamp.dump"
find "$base/dumps" -name 'psst-*.dump' -mtime +14 -delete

repo="$base/psst-db-backup"
cd /www/wwwroot/psst/app
PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst backup export --to "$repo" >/dev/null
cd "$repo"
git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "chore: back up $stamp"
fi
git push -q origin main
echo "Backup $stamp done."
