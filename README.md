# Psst content

The pipeline behind [Psst](../psst-map), a map of the surprising things about specific places: the database schema, the `psst` command for research, review, and publishing, the app's content format, and the server that hosts it.

The content itself lives in a PostgreSQL database on the Psst server. The app downloads static files generated from it.

This repository is public so the code can be read. The content (the places and stories) and its backups are private, and neither the code nor the content is licensed for reuse; see [LICENSE](LICENSE).

## Getting started

```
uv sync
uv run psst status          # checks the connection and shows what's in the database
uv run psst --help
```

Setup (the database password and SSH access) is in [CONTENT_GUIDE.md](CONTENT_GUIDE.md), section 3.

## Doing the work

[CONTENT_GUIDE.md](CONTENT_GUIDE.md) is the handbook for researching a cell, reviewing drafts and problem reports, tagging, and publishing. In short:

```
export PSST_RUN=$(uv run psst run start --kind research --model <model>)
uv run psst research claim --city London           # claim a cell, get a brief in work/<cell>/
uv run psst draft check work/<cell>/draft.json      # every rule a script can check
uv run psst draft submit work/<cell>/draft.json     # stored as drafts, never published directly
# a separate review run:
uv run psst review next --out work/review.json
uv run psst review apply work/decisions.json
# then:
uv run psst publish                                 # export, stage, check, promote
uv run psst bundle                                  # snapshot production into the app before a release
```

## Layout

| path | what |
| --- | --- |
| `psst/` | the `psst` command and everything it does |
| `db/migrations/` | the database schema, applied in order by `psst db migrate` |
| `format/` | JSON schemas: the research draft format and app content format 2 |
| `server/` | server setup, the report API, nginx, backups, and the privacy policy |
| `tests/` | `uv run pytest`: rules, the research pipeline, and proof the migration kept everything |
| `docs/` | [the design](docs/DESIGN.md), [backups and restoring](docs/RESTORE.md), [privacy declarations](docs/PRIVACY.md) |
| `work/`, `export/` | scratch space for briefs, drafts, and exports; not committed |

## Serving the app

`psst publish` writes content format 2 (`format/v2/`) to staging on the server, downloads it back and checks it, and only then promotes it to production at `https://psst.zigao.wang/content/production/v2/`. Pack files are named by their hash and never change; promotion swaps one manifest atomically, and `psst rollback` swaps it back. The app ships with a snapshot and keeps the last good version it downloaded, so a bad or missing update never reaches anyone.

Backups run nightly to the server and to a private GitHub repository, and a restore is tested every week. See [docs/RESTORE.md](docs/RESTORE.md).

## Author

Made by [Zigao Wang](https://www.zigao.wang). Contact: [a@zigao.wang](mailto:a@zigao.wang).
