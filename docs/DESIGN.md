# Psst content platform

How Psst stores, organizes, reviews, and ships its content. This replaces the hand-made area files.

## Goals

- One source of truth that can grow to hundreds of thousands of facts without getting messy.
- Nothing reaches users unless it has been reviewed, published, and checked on staging.
- Every fact can say where it came from, who or what wrote it, and when it was last checked.
- The app keeps working offline, keeps the last good content, and never breaks because the server changed.

## Overview

```
research agents ──> drafts ──> skeptical review ──> published
      │                              (Postgres + PostGIS on the VPS)
      │                                         │
  psst CLI (this repo) ── export ──> staging ── check ──> production ──> app
                                     (static files behind nginx)
```

- **Database:** Postgres 14 with PostGIS on the VPS, in its own database (`psst`) and role. It is the single source of truth.
- **CLI:** `psst`, a Python package in this repository, is the only way agents and people change content. It reaches the database through an SSH tunnel it opens itself, so the database never listens on the internet.
- **App format:** the app never reads the database. `psst publish` generates versioned static files from it, so storage and app formats can change independently.
- **Server:** nginx serves the static files and proxies two small endpoints (problem reports and coverage requests) to a tiny service. No other moving parts.

## Data model

All tables live in the `psst` schema. The SQL is in `db/migrations/`, applied in order by `psst db migrate`.

### Places

`places` holds one row per physical thing someone can walk up to.

- `id` is permanent and global: `pl_` plus 10 characters of Crockford base32 (`pl_7g2k9x4m1q`). Random for new places. For migrated places it's derived from the old `areaId/spotId`, so re-running the migration always produces the same ids.
- `legacy_place_ids` maps every old `areaId/spotId` to its new id. It ships to the app, which rewrites saved places and feed history once on first launch. Nothing a person saved is lost.
- The coordinate is a WGS-84 point (`geometry(Point, 4326)`), with `coord_source` (`wikidata` or `osm`), `coord_source_ref` (`Q123`, `way/456`), and `coord_license` (`CC0-1.0` for Wikidata, `ODbL-1.0` for OpenStreetMap). Wikidata is preferred when both exist, as before. The app shows "© OpenStreetMap contributors" on every place located from OSM, and in Settings.
- `wikidata_id` and `osm_ref` are stored whenever they exist, even when the coordinate came from the other source, and each is unique across all places. That is the primary duplicate check. A second check rejects a new place within 150 meters of an existing place with a similar name (trigram similarity above 0.4).
- `kind` and `size` are unchanged from the file format.
- `h3_cell` (resolution 7) is the research cell. The hierarchy columns are filled in automatically (see below).

`place_names` holds names: `(place_id, lang, name, role)`, where `role` is `display` (the English name used in the app), `local` (the name on the signs, in local script), or `alt` (established names in other languages). Names come from Wikidata labels and OpenStreetMap `name:*` tags, never from translation.

### Facts

`facts` holds one row per story.

- `id` is permanent: `fa_` plus 10 characters. `veracity` is `fact`, `legend`, or `disputed`, exactly as before. `category` is unchanged, including `pop`.
- `state` is the lifecycle: `draft`, `reviewed`, `published`, `retired`. Only `published` facts are exported. A place appears in the app only while it has at least one published fact.
- Provenance on every fact:
  - `researched_at`
  - `researched_by` (the model id, for example `claude-sonnet-5-5`)
  - `research_run` (a row in `pipeline_runs`, with the cell, operator, and notes)
  - `reviewed_at` and `reviewed_by`
  - `review_notes`
  - `published_at`
  - `last_verified_at`
  - `retired_at` and `retire_reason`
- `fact_events` records every state change, edit (with the fields changed), and review flag, with who made it, in which run, and why. A trigger writes it, so nothing can skip it.

### Photos

`images` holds one row per photo of a place, with the same lifecycle as a fact (`draft`, `reviewed`, `published`, `retired`) and the same provenance (the run that added it, the run that reviewed it, notes).

- `kind` is `photo` or `historic`; a historic photo has the `year` it was taken, so the app can show "then and now" later.
- Full attribution, copied from the source's own metadata and never typed: `source` (`commons`, `geograph`, `flickr`, `archive`, or `owner`), `source_ref`, `source_url`, `title`, `author`, `author_url`, `license`, `license_url`. Only free licenses are accepted.
- `alt_text` for VoiceOver, and a focus point (`focus_x`, `focus_y`, 0 to 1 from the top left) the app keeps in view when it crops.
- Our own copies, named by their hash: `full_file` (1,920 px on the long side) and `thumb_file` (640 px), progressive JPEGs with all metadata removed, served from `/images/`.
- `image_events` records every change, like `fact_events`; `image_views` records which photos a review run actually looked at, and approving one it never opened is refused.

### Guide information

`guides` holds the practical information about a place, kept apart from its stories: a one-line `identifier`, a short neutral `about`, and the Wikidata item its key facts came from. It has the same lifecycle and provenance as a fact (`draft`, `reviewed`, `published`, `retired`, with the research and review runs and notes), and `guide_events` records every change. At most one guide per place waits for review; publishing a new one retires the one it replaces.

- `guide_sources` links a guide to `sources`, in order, like `fact_sources`.
- `guide_key_facts` holds one row per value, read from Wikidata by the tools and never typed: the `property` it came from (`P170`, `P571`, ...), an English `label`, the `value` as shown, the `value_id` when it's an item, and a `flag` when a sanity check found it implausible. A review must confirm (`flag_confirmed`) or drop every flagged value before approving.
- `guide_claims` keeps two sessions from writing the same place's guide.

Every new place comes with a guide in its research draft, and a place goes live only once its guide is reviewed. Opening hours, websites, and phone numbers are never stored: the app reads them from Apple Maps on the device.

`sources` stores each URL once: normalized `url`, `title`, `publisher`, `language`, `archived_url`, and the result of the last link check. `fact_sources` links facts to sources in order.

### Tags

- `tags` has a permanent `id` (`tg_` plus 8 characters), one `canonical_name`, a `type` (`person_or_group`, `event`, `era`, `theme`, `movement`), and an optional unique `wikidata_id`.
- `tag_names` holds names in other languages, from Wikidata labels. `tag_labels` holds the canonical name and every other way people write it.
- Every canonical name and alias is also stored normalized: lowercase, accents removed, a leading "the" dropped, punctuation and spacing collapsed. The normalized form is unique across all tags and aliases, so "Beatles" and "The Beatles" can't both exist.
- `fact_tags` links facts to tag ids. Text is never stored on facts.
- New tags only come through `psst tags propose`. It first looks for the same Wikidata id, then the same normalized name or alias, then anything with trigram similarity 0.55 or higher. An exact match returns the existing tag. A similar one blocks the proposal and lists the candidates, unless the proposer marks each as a different thing with `--distinct-from`. Drafts can only use tag ids that exist.
- A tag is exported to the app only once it has published facts on at least 3 places.

### Location hierarchy

Every place gets its country, region, city, district, and neighborhood from its coordinate, automatically.

- **Data:** Who's On First (public domain, multilingual) is loaded into `admin_areas`, with each area's placetype, multilingual names, parent, and geometry.
- **Mapping:** WOF placetypes map to Psst levels: `country` to country, `region` to region, `locality` to city, `borough`, `county`, or `localadmin` to district, and `neighbourhood` or `macrohood` to neighborhood.
- **OpenStreetMap where it's better:** in mainland China, WOF has almost no districts or neighborhoods, so OSM administrative boundaries are loaded for each city (`psst hierarchy load-osm`; admin level 6 is a district, level 8 a neighborhood) and preferred per country and level (`PREFERRED_SOURCE`). A short list of boundaries that aren't really places people name (the River Thames is a WOF "neighbourhood") is excluded by id.
- **Assignment:** the smallest polygon of each level containing the point wins, preferring the configured source. Many WOF neighborhoods are points without a boundary; when no neighborhood polygon contains a place, it gets the nearest neighborhood in the same city within 1.5 km, which is deterministic and data-driven. A district with the same name as its city, or covering nearly all of it, is dropped as meaningless.
- **Speed:** every boundary is cut into small pieces (`admin_area_parts`, `ST_Subdivide`), so assigning all places takes seconds. `psst hierarchy index` rebuilds the pieces.
- **Names:** English names come from Wikidata labels (with the official romanization, P402, where there's no English label). Suffixes such as "District" or "Qu" are stripped, and Chinese names without an English label are romanized to pinyin. Every other language comes from Wikidata and WOF.
- **Rules:** nothing is assigned by hand or by a model. `psst hierarchy assign` runs after every import and every draft submission, and the `admin_assignment` column records which rule placed each level.

### Research cells

Research is split by an H3 grid at resolution 7 (hexagons of about 5 km², roughly 2.5 km across). The grid has no gaps or overlaps anywhere on Earth, so coverage is systematic. `research_cells` tracks each cell's state:

- `open`
- `claimed` (by a pipeline run, with a timeout so a crashed agent doesn't lock it)
- `drafted`
- `reviewed`
- `done`

It also records the city it was planned for, the last research date, and the researcher's notes (including leads they looked at and cut). `psst research plan <city>` creates the cells covering a city boundary; cells that already held places before research by cell start as `done`. `psst coverage` renders an HTML map of every cell colored by state. People browsing the app never see cells.

The Postgres H3 extension isn't packaged for this server, so cells are computed in Python with the `h3` library, and PostGIS stores their polygons for the coverage map. Nothing else depends on the extension.

### Reports and demand

- `reports`: "Report a problem" from the app, with the fact id, a reason, an optional message, and the app version. A report never removes a fact by itself, because anyone can send one. It flags the fact (`needs_review`), which puts it first in `psst review next`, where a reviewer approves, fixes, or retires it. `psst review flag` does the same from the command line.
- `demand`: anonymous "no stories here yet" signals. When someone looks at a city-sized map view with no places at all, the app computes the H3 resolution 5 cell (about 250 km²) at the middle of the view, with the same H3 library the server uses, and sends only that cell id. Nothing is sent while the person's own location is in view, so the signal is never where they are. Each device sends a cell at most once a day and at most 10 cells a day; nginx also rate limits per address in memory. The server refuses anything but a valid resolution 5 cell id, stores no addresses, and keeps only a count per cell per day. `psst research claim` picks open cells in the most viewed areas first, `psst research wanted` lists the most viewed areas (including ones not planned yet), and the coverage map shows the counts as a heat layer. People can turn it off in Settings ("Help choose new areas"). It's declared in the privacy policy and the privacy manifest as product interaction, not linked to the person, not used for tracking.

## Pipeline

Research agents never write SQL. They work through the CLI:

1. `psst research claim` picks the next open cell, in priority order (demand, then neighbors of done cells, then everything else), marks it claimed, and writes a brief to `work/<cell>/`. The brief holds the cell's bounds and neighborhoods, the places already in it and around it, and leads from OpenStreetMap and Wikipedia marked when they're already in Psst. Tags are looked up with `psst tags search`.
2. The agent researches and writes `work/<cell>/draft.json` (`format/draft.schema.json`, explained in CONTENT_GUIDE.md). New places carry their Wikidata or OSM reference; additions to existing places carry the place id.
3. `psst draft check` validates everything a script can check (the old validator's rules, plus duplicates against the database and tag ids against the vocabulary). `psst draft submit` stores the drafts with provenance. The pipeline can only ever create `draft` facts.
4. `psst review next` hands a different run the reported facts, then the drafts. It checks sources and veracity skeptically and writes a decision for each fact: approve, edit (which covers relabeling as legend or disputed), or reject, always with notes on what was checked. `psst review apply` checks every decision against the writing rules and records them all or none. Approved facts become `reviewed`; rejected ones are retired with the reason.
5. `psst publish` moves reviewed facts to `published`, exports, uploads to staging, checks staging, and promotes to production.

Every Saturday `psst sources check` requests every cited link; a source that fails twice in a row flags its facts for review, and sites that refuse scripts are only recorded as blocked. Published facts migrated from the area files are verified by the same review (`psst review next --verify`).

`psst` also has `status`, `places search`, `tags`, `reports list`, `review flag`, `coverage`, `backup`, `hierarchy`, `names`, and `db` commands. Every change goes through a command, is attributed to a run, and lands in `fact_events` (state changes, edits with the fields changed, and flags).

## App format

- **Version 2** is a set of static files under `/content/<channel>/v2/`, where the channel is `staging` or `production`:
  - `manifest.json`: `formatVersion`, `contentVersion`, `generatedAt`, and the list of packs with their size and SHA-256.
  - `packs/common.<hash>.json.gz`: cities, the hierarchy, tags, and the legacy id map.
  - `packs/city-<id>.<hash>.json.gz`: one per city, with its places, names, published facts, and published photos (credit, alt text, focus, and the file names under `/images/`). `images` is optional, so older apps ignore it. So is `guide` (identifier, About, sources, and key facts with their Wikidata properties).
- **Immutability:** pack files are named by their hash, so they never change once written. Promoting to production only swaps `manifest.json`, which is atomic. Old manifests are kept, so `psst rollback` is instant.
- **Offline:** the app ships with a snapshot of production (`psst bundle` copies it into the app before a build), so it works offline from the first launch.
- **Updates:** in the background it fetches the manifest and downloads only the packs whose hashes changed. It checks each hash, decodes everything, and only then swaps the new set in as the current content. Anything that fails leaves the previous content in place. The app always keeps the last good version.
- **Compatibility:** a future incompatible format goes to `/v3/` beside `/v2/`, so older apps keep reading `/v2/` for as long as it's published. Within a version, the decoder ignores unknown fields, maps unknown categories and kinds to a neutral fallback, and skips entries it can't read. The previous app, which only reads bundled version 1 files, is unaffected because it never downloads anything.

### Staging check

Before promotion, `psst publish` downloads the staging files over HTTPS and checks four things:

- Every hash matches.
- Every pack validates against its schema in `format/v2/`, and every reference resolves (places to areas, facts to tags).
- Every legacy id still resolves.
- Every photo file new since production is served under `/images/`.
- The number of published places and facts hasn't dropped by more than 2 percent from production, unless `--allow-shrink` is given with a reason.

## Languages

- Facts are written in US English, researched from the best local sources in any language. One content language, one style guide.
- Place names are multilingual (see `place_names`). The app shows the English name with the local name beside it, and when the device language matches a stored name it shows that too.
- Each fact has a Translate button when the device language isn't English and Apple's Translation framework supports the pair. It translates in place with a "Translated" label and "Show original", caches results on the device, and a setting translates automatically. Translations are never stored on the server. This uses `TranslationSession` on iOS 18 and later. On iOS 17.4 to 17.7 the system translation sheet is used instead.
- The interface uses a String Catalog. English is complete and Simplified Chinese is added as the first translation, which proves the setup. Adding a language means adding it to the catalog, with no code changes.

## Search

Search runs on the device over everything downloaded, so it works offline:

- **Fields:** it matches names in every stored language, neighborhood, district, city, tag names and aliases, kind, and the words of the facts.
- **Normalization:** case and accents are ignored. Traditional Chinese is folded to Simplified, and Chinese names are also indexed as toneless pinyin, with and without spaces, so "heping fandian" and "和平飯店" both find the Peace Hotel.
- **Ranking:** exact name, then name prefix, then other names, then places, then tags, then story text.
- **Fallback:** when nothing matches, the app detects the query's language and translates it to English on the device, then searches again and says so.
- **Results:** grouped by city and neighborhood.

## Hosting

- **Files:** nginx on the VPS serves `psst.zigao.wang` (and `psst.67-230-170-225.sslip.io`, which works before DNS is set up), with TLS from Let's Encrypt, a long cache for packs and photos, and no cache for manifests.
- **Service:** `psst-api` is a small Python service run by systemd, reachable only through nginx, with nginx rate limits. It accepts reports and demand signals and writes them to the database with a role that can do nothing else.
- **Isolation:** the database, role, directory (`/www/wwwroot/psst`), service, and nginx server block are all separate from the other sites on the box.
- **Privacy:** the privacy policy is served at `https://psst.zigao.wang/privacy` and linked in the app. The App Store label declares:
  - coarse location (the demand cell) and usage data, not linked to the person and not used for tracking;
  - the text of problem reports, not linked.

## Backups

Two copies, in two places:

- **On the VPS:** `pg_dump` in custom format every night, 14 days kept.
- **On GitHub:** a deterministic, sorted, plain-text export of every table, committed every night to the private `psst-db-backup` repository. Because it's sorted text, git stores only what changed each day, and every day can be restored. A clone on the Mac is a third copy, refreshed with `git pull`.

The text export keeps only the boundaries places and research cells use, with their parents, since boundaries can be reloaded from their public sources. The dump leaves out `admin_area_parts`, which `psst hierarchy index` rebuilds.

`docs/RESTORE.md` explains both restores step by step, and both were tested. `psst backup test` restores the latest GitHub export into a scratch database with every foreign key checked, exports it again, and compares every table byte for byte. It runs every Sunday from cron.

## Migration (done)

1. The database, roles, schema, and extensions were created (`server/database.sh`), and Who's On First was loaded for GB, MY, and CN, plus OpenStreetMap boundaries for Shanghai.
2. `psst import legacy` read all 47 area files, including the London seeding output.
   - Each spot became a place with an id derived from its old `areaId/spotId`, plus a legacy id row. Its facts became published facts with provenance: the date from git history, the model that wrote them (`claude-opus-5-5` before the London seeding began, `claude-sonnet-5-5` after), a `legacy-import` run, and `reviewed_by = legacy-validator`.
   - Sources were deduplicated by normalized URL. No two spots turned out to be the same place.
3. Multilingual names were fetched from Wikidata and OSM, every place got its city and neighborhood, and every fact was read by a tagging pass (four parallel runs, then one cleanup run for merges and renames).
4. `tests/test_migration.py` proves nothing was lost or changed. It compares the database and what production serves against the area files at the `legacy-areas` tag of the private `psst-content-archive` repository: every place, coordinate, fact text, source link, and old id.
5. The result was published through staging and bundled into the app, which rewrites old saved place ids on first launch.
6. `areas/` and the old scripts were removed. This repository's history was then rewritten without the area files so the code could be public; the full history, stories included, is kept in the private `psst-content-archive` repository.

## Why this, and not something else

- **Why not keep JSON files in git:** it doesn't scale to lifecycle states, provenance, shared sources, tag integrity, or spatial queries. The database enforces what the old validator could only check after the fact.
- **Why not a hosted database or CMS:** the VPS already runs Postgres and costs nothing extra, PostGIS does the spatial work well, and static files keep the public side simple and cheap to serve.
- **Why not per-area downloads:** areas are gone, and cities are the unit people browse. City packs keep downloads small enough to update over mobile data.
- **Why grid cells for research and not neighborhoods:** neighborhood boundaries are uneven and often missing. A hexagon grid guarantees full coverage, makes progress measurable, and gives parallel agents non-overlapping work.
