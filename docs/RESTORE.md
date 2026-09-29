# Backups and restoring

Psst's database lives on the VPS (`ssh bwh`). If it's damaged or lost, the app keeps working: phones keep
the last content they downloaded, and the published files under `/www/wwwroot/psst/public/content` are
plain static files. What a restore brings back is the ability to research, review, and publish.

## What is backed up

| Copy | Where | Kept | Has |
| --- | --- | --- | --- |
| Nightly dump | `/www/wwwroot/psst/backup/dumps/psst-<time>.dump` on the VPS | 14 days | The whole database, including every boundary, but not `admin_area_parts` (an index `psst hierarchy index` rebuilds) |
| Nightly text backup | The private GitHub repository `ZigaoWang/psst-db-backup` | Forever (git history) | Every table as a sorted CSV. Boundaries: only the ones places and research cells use, with their parents |

Both run at 03:17 UTC from `/etc/cron.d/psst-backup` (`server/backup.sh`) and log to
`/var/log/psst-backup.log`. Every Sunday at 04:47 UTC, `psst backup test` restores the latest text backup
into a scratch database, exports it again, and checks every table matches byte for byte. Look for
"Restore test passed" in the log. The VPS pushes to GitHub with its own deploy key (SSH host alias
`github-psst-backup` in `/root/.ssh/config`), which can write to that one repository and nothing else.

To run either by hand, as root on the VPS:

```
/usr/local/bin/psst-backup
cd /www/wwwroot/psst/app && PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst backup test
```

## Case 1: the VPS is fine, the data is wrong

Someone deleted or damaged rows. Restore last night's dump into a new database, check it, then swap.

```
ssh bwh
cd /tmp
ls /www/wwwroot/psst/backup/dumps/                         # pick the dump from before the damage
sudo -u postgres createdb -O psst -T template0 psst_restored
sudo -u postgres pg_restore -d psst_restored /www/wwwroot/psst/backup/dumps/psst-<time>.dump
sudo -u postgres psql -d psst_restored -c "SELECT count(*) FROM psst.facts"   # sanity check
```

Then swap them. This stops the report API for a few seconds.

```
systemctl stop psst-api
sudo -u postgres psql -c "ALTER DATABASE psst RENAME TO psst_damaged"
sudo -u postgres psql -c "ALTER DATABASE psst_restored RENAME TO psst"
systemctl start psst-api
cd /www/wwwroot/psst/app && PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst hierarchy index
```

Drop `psst_damaged` once you're sure (`sudo -u postgres dropdb psst_damaged`). Reports filed between the
dump and the swap are lost; that's at most a day of them.

## Case 2: the VPS is gone

Rebuild from the GitHub text backup. On a fresh Ubuntu 22.04 server with the `bwh` SSH alias pointing at it:

1. From your Mac, in this repository: `sh server/deploy.sh` (copies the code; install uv on the server
   first with `curl -LsSf https://astral.sh/uv/install.sh | sh` if it isn't there).
2. On the server, as root, in `/www/wwwroot/psst/app`:
   ```
   sh server/database.sh       # PostgreSQL, PostGIS, roles, an empty database with the schema
   ```
3. Give the server a deploy key for the backup repository: `ssh-keygen -t ed25519 -f /root/.ssh/psst-backup`,
   add the public key to `ZigaoWang/psst-db-backup` on GitHub (Settings, Deploy keys, allow write), and add
   to `/root/.ssh/config`:
   ```
   Host github-psst-backup
     HostName github.com
     User git
     IdentityFile /root/.ssh/psst-backup
   ```
4. Restore. `backup restore` needs an empty database, so point it at a new one and rename it after:
   ```
   git clone github-psst-backup:ZigaoWang/psst-db-backup.git /tmp/psst-db-backup
   PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst backup restore --from /tmp/psst-db-backup --database psst_restored
   sudo -u postgres psql -c "DROP DATABASE psst" -c "ALTER DATABASE psst_restored RENAME TO psst"
   ```
   To restore an earlier day, `git -C /tmp/psst-db-backup checkout <commit>` first; each commit is one night.
   The restore loads with every foreign key checked, so if it finishes, the data is consistent.
5. Reload the full boundaries (the text backup only has the ones in use), then index them:
   ```
   mkdir -p /www/wwwroot/psst/wof && cd /www/wwwroot/psst/wof
   for c in gb my cn; do curl -sLO https://data.geocode.earth/wof/dist/sqlite/whosonfirst-data-admin-$c-latest.db.bz2 && bunzip2 -f whosonfirst-data-admin-$c-latest.db.bz2; done
   cd /www/wwwroot/psst/app
   for c in GB MY CN; do PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst hierarchy load-wof /www/wwwroot/psst/wof/whosonfirst-data-admin-$(echo $c | tr A-Z a-z)-latest.db --country $c; done
   PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst hierarchy load-osm --bounds 30.68,120.85,31.87,122.12 --country CN
   PSST_CONFIG=/www/wwwroot/psst/.env /root/.local/bin/uv run --no-dev psst hierarchy index
   ```
   Add a `load-wof` line for every country Psst covers by then (`SELECT DISTINCT country_code FROM psst.places`).
   Places keep the areas they were assigned; the reload only matters for places added from now on.
6. `sh server/setup.sh` (nginx, TLS, the API, the backup cron), then point DNS at the new server.
7. Put the content back: from your Mac, `psst publish --no-new-facts`. Export is deterministic, so this
   rebuilds exactly what production served. Apps that already have that version download nothing.

## Checking a restore

`psst backup test` is the check: it restores into a scratch database and compares every table. After a
real restore, also run `uv run pytest` from your Mac; the migration tests compare the database against
the original area files and against what production serves.
