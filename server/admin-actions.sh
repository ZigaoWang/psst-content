#!/bin/sh
# Carries out what was asked for on the admin page: Refresh, Check, Publish, or Roll back. server/api.py leaves a
# request file in $run; cron runs this every minute (server/psst-backup.cron). The result of Publish and Roll
# back, with its full output, goes to /admin/action.json for the page to show.
set -u
run=/www/wwwroot/psst/run
admin=/www/wwwroot/psst/public/admin
cd /www/wwwroot/psst/app || exit 1
export PSST_CONFIG=/www/wwwroot/psst/.env PSST_SSH_HOST=local LC_ALL=C.UTF-8
psst() { /root/.local/bin/uv run --no-dev psst "$@"; }

status() {  # action state exit-code log-file
  python3 - "$@" <<'PY'
import json, os, sys, time
action, state, code, log = sys.argv[1:5]
out = open(log).read()[-20000:] if os.path.exists(log) else ""
path = "/www/wwwroot/psst/public/admin/action.json"
previous = json.load(open(path)) if os.path.exists(path) else {}
data = {"action": action, "state": state, "exit": None if code == "" else int(code), "output": out,
        "started": previous.get("started") if state != "running" else time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "finished": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) if state != "running" else None}
tmp = path + ".tmp"
json.dump(data, open(tmp, "w"))
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}

act() {  # name, then the psst command
  name=$1; shift
  rm -f "$run/$name"
  log=$(mktemp)
  status "$name" running "" "$log"
  flock -w 600 /run/psst-publish.lock sh -c 'cd /www/wwwroot/psst/app && /root/.local/bin/uv run --no-dev psst "$@"' psst "$@" > "$log" 2>&1
  code=$?
  status "$name" "$([ $code -eq 0 ] && echo done || echo failed)" "$code" "$log"
  rm -f "$log"
  echo "$(date -u '+%F %T') $name exited $code" >> /var/log/psst-admin.log
}

[ -e "$run/check" ] && act check publish --only-staging
[ -e "$run/publish" ] && act publish publish
[ -e "$run/rollback" ] && act rollback rollback
if [ -e "$run/admin-refresh" ]; then
  rm -f "$run/admin-refresh"
  flock -w 300 /run/psst-admin.lock /root/.local/bin/uv run --no-dev psst admin --out "$admin" >> /var/log/psst-admin.log 2>&1
fi
exit 0
