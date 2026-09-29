#!/bin/sh
# Weekly restore test, run by cron as root (see psst-backup.cron). Restores the latest GitHub backup into a
# scratch database and checks every table matches. The result goes into the next night's STATUS file,
# which the GitHub check in psst-db-backup reads.
set -u
export LC_ALL=C.UTF-8
status=/www/wwwroot/psst/backup/restore-test.status
cd /www/wwwroot/psst/app
if PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst backup test; then
  result=passed
else
  result=failed
fi
echo "$result $(date -u +%Y-%m-%dT%H%MZ) $(date -u +%s)" > "$status"
[ "$result" = passed ]
