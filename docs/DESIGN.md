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
- The coordinate is a WGS-84 `geography(Point)`, with `coord_source` (`wikidata` or `osm`), `coord_source_ref` (`Q123`, `way/456`), and `coord_license` (`CC0-1.0` for Wikidata, `ODbL-1.0` for OpenStreetMap). Wikidata is preferred when both exist, as before. The app shows "© OpenStreetMap contributors" on every place located from OSM, and in About.
- `wikidata_id` and `osm_ref` are stored whenever they exist, even when the coordinate came from the other source, and each is unique across all places. That is the primary duplicate check. A second check flags any draft within 25 meters of an existing place whose name is similar (trigram similarity 0.5 or higher).
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
- `fact_events` records every state change with who made it and why, so history is never overwritten.

`sources` stores each URL once: normalized `url`, `title`, `publisher`, `language`, `archived_url`, and the result of the last link check. `fact_sources` links facts to sources in order.

### Tags

- `tags` has a permanent `id` (`tg_` plus 8 characters), one `canonical_name`, a `type` (`person_or_group`, `event`, `era`, `theme`, `movement`), and an optional unique `wikidata_id`.
- `tag_names` holds names in other languages, from Wikidata labels. `tag_aliases` holds the other ways people write it.
- Every canonical name and alias is also stored normalized: lowercase, accents removed, a leading "the" dropped, punctuation and spacing collapsed. The normalized form is unique across all tags and aliases, so "Beatles" and "The Beatles" can't both exist.
- `fact_tags` links facts to tag ids. Text is never stored on facts.
- New tags only come through `psst tags propose`. It first looks for the same Wikidata id, then the same normalized name or alias, then anything with trigram similarity 0.55 or higher. It returns the existing tag when any of those match, and only creates a new one when none do. Research agents can only use ids that exist.
- A tag is exported to the app only once it has published facts on at least 3 places.

### Location hierarchy

Every place gets its country, region, city, district, and neighborhood from its coordinate, automatically.

- **Data:** Who's On First (public domain, multilingual) is loaded into `admin_areas`, with each area's placetype, multilingual names, parent, and geometry.
- **Mapping:** WOF placetypes map to Psst levels: `country` to country, `region` to region, `locality` to city, `borough`, `county`, or `localadmin` to district, and `neighbourhood` or `macrohood` to neighborhood.
- **Assignment:** the smallest WOF polygon of each level containing the point wins. Many WOF neighborhoods are points without a boundary. When no neighborhood polygon contains a place, it gets the nearest neighborhood point in the same city within 1.5 km, which is deterministic and data-driven. When WOF has nothing for a level, OpenStreetMap administrative boundaries are the fallback. Wikidata is used only for names.
- **Rules:** nothing is assigned by hand or by a model. `psst hierarchy assign` runs after every import and every draft submission, and the `admin_assignment` column records which rule placed each level.

### Research cells

Research is split by an H3 grid at resolution 7 (hexagons of about 5 km², roughly 2.5 km across). The grid has no gaps or overlaps anywhere on Earth, so coverage is systematic. `research_cells` tracks each cell's state:

- `open`
- `claimed` (by a pipeline run, with a timeout so a crashed agent doesn't lock it)
- `drafted`
- `reviewed`
- `done`

It also records place and fact counts and the last research date. `psst coverage` renders an HTML map of every cell colored by state. People browsing the app never see cells.

The Postgres H3 extension isn't packaged for this server, so cells are computed in Python with the `h3` library, and PostGIS stores their polygons for the coverage map. Nothing else depends on the extension.

### Reports and demand

- `reports`: "Report a problem" from the app, with the fact id, a reason, an optional message, and the app version. A report never removes a fact by itself, because anyone can send one. It puts the fact on the review queue (`psst review reports`), where a reviewer confirms, fixes, or retires it.
- `demand`: anonymous "no stories here yet" signals. When someone looks at a city-sized map area with no places, the app sends the H3 resolution 5 cell (about 250 km²), and nothing else. People can turn this off in Settings. It's declared in the privacy policy and the App Store privacy label.

## Pipeline

Research agents never write SQL. They work through the CLI:

1. `psst research claim` picks the next open cell, in priority order (demand, then neighbors of done cells, then everything else), marks it claimed, and writes a brief to `work/<cell>/`. The brief holds the cell's bounds, existing places, sweep candidates from OpenStreetMap and Wikipedia, and the tag vocabulary.
2. The agent researches and writes `work/<cell>/drafts.json` (format in CONTENT_GUIDE.md). New places carry their Wikidata or OSM reference; additions to existing places carry the place id.
3. `psst draft check` validates everything a script can check (the old validator's rules, plus duplicates against the database and tag ids against the vocabulary). `psst draft submit` stores the drafts with provenance. The pipeline can only ever create `draft` facts.
4. `psst review next` hands a different run the drafts for a cell. It checks sources and statuses skeptically and writes a verdict for each fact: approve, fix, relabel as legend or disputed, or reject. `psst review apply` records the verdicts. Approved facts become `reviewed`.
5. `psst publish` moves reviewed facts to `published`, exports, uploads to staging, checks staging, and promotes to production.

`psst` also has `places`, `facts`, `sources`, `tags`, `reports`, `stats`, `coverage`, `backup`, and `db` subcommands for inspection and repair.

## App format

- **Version 2** is a set of static files under `/content/<channel>/v2/`, where the channel is `staging` or `production`:
  - `manifest.json`: `formatVersion`, `contentVersion`, `generatedAt`, and the list of packs with their size and SHA-256.
  - `packs/common.<hash>.json.gz`: cities, the hierarchy, tags, and the legacy id map.
  - `packs/city-<id>.<hash>.json.gz`: one per city, with its places, names, and published facts.
- **Immutability:** pack files are named by their hash, so they never change once written. Promoting to production only swaps `manifest.json`, which is atomic. Old manifests are kept, so `psst rollback` is instant.
- **Offline:** the app ships with a snapshot of production (`psst bundle` copies it into the app before a build), so it works offline from the first launch.
- **Updates:** in the background it fetches the manifest and downloads only the packs whose hashes changed. It checks each hash, decodes everything, and only then swaps the new set in as the current content. Anything that fails leaves the previous content in place. The app always keeps the last good version.
- **Compatibility:** a future incompatible format goes to `/v3/` beside `/v2/`, so older apps keep reading `/v2/` for as long as it's published. Within a version, the decoder ignores unknown fields, maps unknown categories and kinds to a neutral fallback, and skips entries it can't read. The previous app, which only reads bundled version 1 files, is unaffected because it never downloads anything.

### Staging check

Before promotion, `psst publish` downloads the staging files over HTTPS and checks four things:

- Every hash matches.
- Every pack validates against `format/v2.schema.json`.
- Every legacy id still resolves.
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

- **Files:** nginx on the VPS serves `psst.zigao.wang`, with TLS from Let's Encrypt, a long cache for packs, and no cache for manifests.
- **Service:** `psst-api` is a small Python service run by systemd, reachable only through nginx, with nginx rate limits. It accepts reports and demand signals and writes them to the database with a role that can do nothing else.
- **Isolation:** the database, role, directory (`/www/wwwroot/psst`), service, and nginx server block are all separate from the other sites on the box.
- **Privacy:** the privacy policy is served at `https://psst.zigao.wang/privacy` and linked in the app. The App Store label declares:
  - coarse location (the demand cell) and usage data, not linked to the person and not used for tracking;
  - the text of problem reports, not linked.

## Backups

Two copies, in two places:

- **On the VPS:** `pg_dump` in custom format every night, 14 days kept.
- **On GitHub:** a deterministic, sorted, plain-text export of every table, committed every night to the private `psst-db-backup` repository. Because it's sorted text, git stores only what changed each day, and every day can be restored. A clone on the Mac is a third copy, refreshed with `git pull`.

`docs/RESTORE.md` explains both restores step by step. `psst backup test` restores the latest GitHub export into a scratch database and compares row counts and checksums with the live one. It was run as part of this change.

## Migration plan

1. Create the database, role, schema, and extensions. Load Who's On First for GB, MY, and CN.
2. `psst import legacy areas/` reads every area file, including those written by the London researchers.
   - Each spot becomes a place with its derived id and a legacy id row. Its facts become published facts with provenance: the area's `researchedOn`, the model that wrote them (`claude-opus-5-5` for the first 11 areas, `claude-sonnet-5-5` for the London seeding), a `legacy-import` pipeline run, and `reviewed_by = legacy-validator`.
   - Sources are deduplicated by normalized URL.
   - Spots that turn out to be the same thing across area files (same Wikidata id or OSM reference) are merged, and both legacy ids point at the one place.
3. Fetch multilingual names from Wikidata and OSM, assign the hierarchy and cells, and tag every fact.
   - A tagging pass reads each place's facts and chooses tags through `psst tags propose`, which enforces the vocabulary rules above.
4. Tests prove nothing was lost or changed. Every legacy spot resolves to a place, every legacy fact is present with byte-identical text, and every source URL and every link between facts and sources survives. The exported packs round-trip to the same content.
5. Publish to staging, check, promote, bundle into the app, and ship the app with the legacy id map.
6. Remove `areas/`, the old scripts, and the area-based guide once the tests pass on the final import, after the running researchers finish.

## Why this, and not something else

- **Why not keep JSON files in git:** it doesn't scale to lifecycle states, provenance, shared sources, tag integrity, or spatial queries. The database enforces what the old validator could only check after the fact.
- **Why not a hosted database or CMS:** the VPS already runs Postgres and costs nothing extra, PostGIS does the spatial work well, and static files keep the public side simple and cheap to serve.
- **Why not per-area downloads:** areas are gone, and cities are the unit people browse. City packs keep downloads small enough to update over mobile data.
- **Why grid cells for research and not neighborhoods:** neighborhood boundaries are uneven and often missing. A hexagon grid guarantees full coverage, makes progress measurable, and gives parallel agents non-overlapping work.
