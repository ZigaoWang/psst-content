#!/bin/sh
# Nightly Psst backup, run by cron as root (see psst-backup.cron):
#  1. a pg_dump of the whole database (less one rebuildable index), kept 14 days on this server;
#  2. a sorted plain-text export committed and pushed to the private GitHub repo psst-db-backup, with a
#     STATUS file that a GitHub Actions check in that repo reads every morning. If this script fails or
#     stops running, STATUS goes stale and the check fails, and GitHub emails the owner.
set -eu
export LC_ALL=C.UTF-8
base=/www/wwwroot/psst/backup
repo="$base/psst-db-backup"
stamp=$(date -u +%Y-%m-%dT%H%MZ)
# GitHub refuses files over 100 MB; stop well before that.
max_file_bytes=$((90 * 1024 * 1024))
warnings=""

mkdir -p "$base/dumps"
cd /tmp
# admin_area_parts is only a lookup index, rebuilt by `psst hierarchy index`; its data is left out.
sudo -u postgres pg_dump -Fc --exclude-table-data=psst.admin_area_parts psst > "$base/dumps/psst-$stamp.dump.tmp"
mv "$base/dumps/psst-$stamp.dump.tmp" "$base/dumps/psst-$stamp.dump"
find "$base/dumps" -name 'psst-*.dump' -mtime +14 -delete
dump_bytes=$(stat -c %s "$base/dumps/psst-$stamp.dump")
disk_free_bytes=$(df --output=avail -B1 "$base" | tail -1 | tr -d ' ')

cd "$repo"
git pull -q --rebase origin main
cd /www/wwwroot/psst/app
PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst backup export --to "$repo" >/dev/null
cd "$repo"

# A file too big for GitHub keeps its last good copy, and the check reports it.
for f in *.csv; do
  size=$(stat -c %s "$f")
  if [ "$size" -gt "$max_file_bytes" ]; then
    git checkout -q -- "$f" 2>/dev/null || true
    warnings="$warnings $f is $size bytes, over the limit, so its last good copy was kept;"
  fi
done
largest=$(ls -S *.csv | head -1)

restore="never run"
if [ -f "$base/restore-test.status" ]; then restore=$(cat "$base/restore-test.status"); fi

cat > STATUS <<STATUS
last_backup=$stamp
last_backup_epoch=$(date -u +%s)
export_bytes=$(du -cb *.csv | tail -1 | cut -f1)
largest_file=$largest $(stat -c %s "$largest")
dump_bytes=$dump_bytes
disk_free_bytes=$disk_free_bytes
restore_test=$restore
warnings=${warnings:-none}
STATUS

git add -A
git commit -q -m "chore: back up $stamp"
git push -q origin main
echo "Backup $stamp done.${warnings:+ Warnings:$warnings}"
