# Psst platform design

Status: draft for the owner's approval. Version 1, October 9, 2026.

This document specifies the new Psst content platform: the database, the curation pipeline, the agent workflow, the admin console, the public website, and the output the apps read. Nothing is built until the owner approves it. Section 18 lists the decisions still open.

## Contents

1. Purpose
2. Principles
3. Decisions already made
4. System overview
5. Data model
6. Lifecycle
7. Evidence and checking
8. Tasks and agents
9. The rulebook
10. Places, areas, and coordinates
11. Publishing and the published output
12. Admin console
13. Website
14. Security
15. Environments, testing, and operations
16. Repository layout
17. Moving from the current system
18. Open decisions
19. Milestones

## 1. Purpose

Psst's value is its curated content: places, the surprising stories about them, and plain guide information, each traceable to sources and checked. The current system grew in layers (JSON files, then a database, then photos and guides each copying the fact workflow) and its accuracy depends on how carefully a model reads. The new platform makes accuracy structural, keeps every change traceable, scales to hundreds of thousands of places, and serves the iOS app and a public website from one output.

Out of scope for this document: the iOS app's own redesign, which follows once the platform and website exist.

## 2. Principles

1. **Evidence before prose.** Every claim carries the passage that supports it. Tools check what tools can check; models answer narrow questions; people decide the hard cases.
2. **Nothing is overwritten.** Content changes by adding revisions. Any past state can be read back.
3. **One mechanism per job.** One lifecycle, one task system, one rulebook, one publishing path, used by every content type.
4. **The database enforces the rules.** Lifecycle transitions, attribution, and permissions are enforced by the database, not by each script.
5. **Measured quality.** Accuracy is a number per run, per task type, per model, and per city. Publishing requires it to be under a threshold.
6. **Least privilege.** Every role can do only its job. Agents can't publish; the website can only read.
7. **Plain everywhere.** Content, console, docs, and errors are written plainly, in US English, without dashes, hype, or filler.
8. **Built to grow.** No path loads everything at once. New content types, languages, and cities are configuration, not new code paths.

## 3. Decisions already made

| decision | choice |
| --- | --- |
| Existing content | Used as research leads only. Nothing is copied as content; every published fact is researched and checked again under the new rules. |
| App during the rebuild | Shows only new, checked content. The first publish from the new platform replaces the current content; until then the app is unchanged. |
| Web stack | SvelteKit for both the public website and the admin console. |
| Database | A new PostgreSQL database with PostGIS on the current server, built beside the old one. |
| Accuracy | The mechanism in section 7, not stronger models. |

## 4. System overview

```
           sources (web, Wikidata, OSM, archives)
                         │
  agents ──► psst CLI ──► Postgres (psst_platform) ◄── admin console (SvelteKit, private)
  (Claude    tasks,       content, evidence, tasks,        │
  sessions)  checks       runs, audits, history            ▼
                         │                          owner's actions
                         ▼
                     publisher ──► staging ──► acceptance ──► production output
                                                                    │
                                                    ┌───────────────┴──────────────┐
                                                    ▼                              ▼
                                               iOS app                     website (SvelteKit, public)
```

- **Postgres** holds everything that is curated. It's the single source of truth.
- **The psst CLI** is the only way agents and people write content. It talks to the database through functions that enforce the lifecycle (section 6).
- **Agents** are Claude Code sessions. They ask for tasks, do them, and submit structured results (section 8).
- **The publisher** builds the output, stages it, checks it, and promotes it (section 11).
- **The admin console** is a private web app for watching, auditing, and acting (section 12).
- **The website** is a public, mostly static site built from the published output (section 13).

## 5. Data model

All tables live in schema `psst` of database `psst_platform`. Ids are permanent, prefixed, and random (Crockford base32): `pl_` place, `it_` item, `rv_` revision, `cl_` claim, `so_` source, `sn_` snapshot, `tk_` task, `ru_` run, `tg_` tag, `im_` image file, `pb_` publication. Times are `timestamptz` in UTC.

### 5.1 Places

| table | purpose |
| --- | --- |
| `places` | One physical thing with a pin: `id`, `kind`, `size`, `geom` (WGS-84 point), `coord_source` (`wikidata` or `osm`), `coord_ref`, `coord_license`, `wikidata_id` (unique), `osm_ref` (unique), `h3_r7`, `country_code`, area ids (section 10), `state` (`active`, `merged`, `gone`), `merged_into`. |
| `place_names` | `place_id`, `lang`, `role` (`display`, `local`, `alt`), `name`, `source`. |
| `place_identity` | Old ids that point to a place (`legacy_id`, `place_id`), so saved places in the app survive (section 17). |

A place exists independently of its content. It appears in the output only while it has published content.

### 5.2 Content items and revisions

Every piece of curated content is an **item** with an immutable chain of **revisions**. This one structure replaces the separate fact, guide, and image workflows.

| table | purpose |
| --- | --- |
| `item_types` | Configuration: `story`, `guide`, `photo` today; `tour`, `audio` later. Each has a JSON schema for its revision body, its check plan (section 7), and its acceptance threshold. |
| `items` | `id`, `type`, `place_id` (or null for items about several places, such as tours), `state` (section 6), `current_revision`, `published_revision`, `position`, `created_by_run`. |
| `revisions` | `id`, `item_id`, `number`, `body` (JSONB, validated against the type's schema), `language` (`en` for now), `created_by_run`, `created_by_task`, `created_at`, `reason`. Never updated or deleted. |

Revision bodies by type:

- **story:** `category`, `veracity` (`fact`, `legend`, `disputed`), `headline`, `short`, `long`, `tags`.
- **guide:** `identifier`, `about`, and `key_facts` (each a property id and a structured value, section 7.5).
- **photo:** `file`, `kind` (`photo`, `historic`), `year`, `alt`, `focus`, and the credit fields copied from the source's metadata.

An edit creates a new revision. Approving, publishing, retiring, and reverting move pointers and states; they never change a revision.

### 5.3 Claims and evidence

| table | purpose |
| --- | --- |
| `claims` | One checkable statement in a revision: `id`, `revision_id`, `n`, `text` (the claim in plain words), `kind` (`date`, `name`, `number`, `place`, `event`, `attribute`), `value` (structured, for dates, numbers, and properties), `span` (where it occurs in the prose). |
| `evidence` | `claim_id`, `snapshot_id`, `quote` (exact text from the snapshot), `quote_start`, `quote_end`, `matched` (set by the tools, section 7.2), `language`. A claim needs at least one matched evidence row. |
| `sources` | One per canonical URL: `id`, `url`, `url_key`, `title`, `publisher`, `kind` (`official_record`, `owner`, `museum_or_academic`, `press`, `reference`, `community`), `language`. |
| `snapshots` | What the source said when read: `id`, `source_id`, `fetched_at`, `http_status`, `via` (`live`, `archive`), `archive_url`, `content_hash`, `text` (compressed, extracted plain text). Immutable. |

The source `kind` drives review rules: an `official_record` (heritage listing, archive, survey) supports a plain fact on its own; anecdotes need independent corroboration or are labeled legend (section 9).

### 5.4 Checks, audits, and decisions

| table | purpose |
| --- | --- |
| `checks` | One verdict on one claim: `claim_id`, `kind` (`tool`, `match`, `escalation`, `audit`, `owner`), `run_id`, `model`, `verdict` (`supported`, `unsupported`, `contradicted`, `unclear`), `note`, `created_at`. |
| `decisions` | One decision on one revision: `revision_id`, `run_id`, `decision` (`accept`, `revise`, `reject`), `reason`, `created_at`. Written by the check workflow, not typed freely. |
| `audits` | A random sample of a run's accepted work rechecked: `run_id`, `sample_size`, `errors`, `rate`, `threshold`, `outcome` (`passed`, `failed`). A failed audit reopens the run's work automatically. |
| `model_accuracy` | A view over checks and audits: measured error rate per task type and model, for routing (section 8.4). |

### 5.5 Tags, areas, and reference data

- `tags`, `tag_labels`, `tag_names`, `item_tags`: as today (canonical names, normalized aliases, Wikidata links), attached to items instead of facts.
- `areas`, `area_names`, `area_parts`: the boundary hierarchy, loaded as today from Who's On First and OpenStreetMap.
- `research_cells`: the H3 research grid and each cell's coverage.
- `leads`: candidate places for research, from Wikipedia, OpenStreetMap, Wikidata, and the old content (section 17), with their fame and status.

### 5.6 Work, runs, and history

| table | purpose |
| --- | --- |
| `runs` | A session doing work: `id`, `kind` (`agent`, `owner`, `system`), `model`, `operator`, `started_at`, `ended_at`, `notes`. |
| `tasks` | Section 8. |
| `transitions` | Every state change of every item, written only by the lifecycle functions: `item_id`, `from_state`, `to_state`, `revision_id`, `run_id`, `task_id`, `reason`, `at`. |
| `reports` | Problem reports from readers, attached to an item. |
| `demand` | Anonymous counts of empty areas viewed, as today. |
| `publications` | Every staged and promoted output version with its manifest, counts, checks, and run. |
| `link_checks` | Each weekly check of each source: status, and whether the text changed from its snapshot. |

History is complete by construction: revisions are immutable, transitions are written by the database, and every row names its run.

### 5.7 Growth

- History tables (`transitions`, `checks`, `snapshots`) are partitioned by month.
- Snapshot text is stored compressed; large or rarely read snapshots can move to object storage with only a hash and pointer left in the table.
- Every list the console or website shows has an index that matches its filter and order.

## 6. Lifecycle

Every item follows one state machine, enforced by database functions. Agents and the console call the functions; no role can update `state` directly.

```
draft ──submit──► checking ──all claims pass──► accepted ──publish──► published
  ▲                  │                              │                    │
  │               problems                       audit fails          report, link change,
  └──── revise ◄─────┘◄─────────────────────────────┘◄──────────────── or new revision
                                                                         │
                                       any state ──retire (with reason)──► retired
```

| state | meaning | who moves it |
| --- | --- | --- |
| `draft` | A revision being written or revised. | writer tasks |
| `checking` | Submitted; tool checks and claim checks running. | the check workflow |
| `accepted` | Every claim supported; waiting for its run to pass audit and for publishing. | the check workflow |
| `published` | In the output. A newer accepted revision replaces it at the next publish. | the publisher |
| `retired` | Taken down or never accepted, with a reason. Kept forever. | check workflow, owner |

Rules the functions enforce:

- A run never checks or audits its own writing.
- A revision moves to `accepted` only when every claim has at least one `supported` verdict from the claim checks and no unresolved `contradicted` verdict.
- A run's accepted work publishes only after its audit passes (section 7.6).
- A reader report or a changed source moves a published item's claims back to checking; the published revision stays live until a replacement is accepted or the owner retires it.

## 7. Evidence and checking

This is the core of the design. The October 9 spot check showed that 10 to 17 percent of a cheaper model's approvals were wrong, and that the errors were misreadings (a replica dated as the original, a closing year given as the build year), not invented sources. The mechanism catches them by structure.

### 7.1 Writing with evidence

A writer task returns, for each item, the prose and its claims. Each claim lists its evidence: the source, and the exact passage copied from the snapshot the tools saved when the writer read it. Writers read sources only through `psst fetch`, which creates the snapshot.

### 7.2 Tool checks (no model)

On submit, the tools refuse a revision unless:

1. Every quoted passage appears in its snapshot (exact match after whitespace and Unicode normalization; for CJK text, exact substring).
2. Every year, number, and proper name in the prose appears in at least one of its claims, and every claim's value appears in at least one of its passages.
3. The prose follows the rulebook (section 9): lengths, forbidden words, spelling, the identifier shape.
4. Structured values agree: a guide's identifier year matches its build or opening claim; key facts agree with claims or are flagged.
5. Source rules hold: at least one passage from a source that is not a reference work for each story claim; an anecdote with one source is labeled legend.

These checks are free and deterministic. They would have caught most of the errors found on October 9 before any model looked.

### 7.3 Claim checks (narrow, independent)

Each claim is then checked twice, independently: a checker sees only the claim and its passages (a few sentences), never the prose or the writer's reasoning, and answers `supported`, `unsupported`, `contradicted`, or `unclear` with a one-line note. The two checks use different instructions (one asks whether the passage says this, the other asks what the passage actually says about it and compares). Cheap models do this narrow task well.

- Both `supported`: the claim passes.
- Any disagreement or `unclear`: an escalation check by a stronger model, with the full snapshot available.
- `unsupported` or `contradicted` after escalation: the revision goes back to the writer with the reason, or the claim is removed.

### 7.4 Whole-item checks

Some errors live between claims: a headline that overstates, a short version that says "the first" when the claim says "one of the first", a replica described as the original. One check per revision reads the prose against its claim list and answers: does the prose say anything its claims don't, or say it more strongly? Disagreements escalate the same way.

### 7.5 Guide key facts

Key facts come from Wikidata as structured values with their property. Each becomes a claim whose evidence is the Wikidata statement (a snapshot of the item) plus, where possible, a passage from a non-Wikidata source. A key fact without agreeing evidence is dropped automatically, not left for a reviewer.

### 7.6 Acceptance sampling

Every run's accepted work is audited before it can publish: a random sample (size from the run's volume, at least 30) is rechecked by a stronger model from the snapshots. If the measured error rate is above the type's threshold (owner-set, for example 1 in 50 for stories, 1 in 30 for guides), the audit fails and everything that run accepted goes back to checking. Results feed `model_accuracy`.

### 7.7 Owner's checks

The console offers the same sample view: claim, passages, verdicts, one click to mark a claim wrong. An owner verdict outranks every model verdict and counts in the measured rates.

## 8. Tasks and agents

### 8.1 Task types

| type | input | output |
| --- | --- | --- |
| `research_cell` | a cell, its leads, nearby places | new places, story items in draft, skipped leads with reasons |
| `write_story` | a place, a lead or angle | one story revision with claims and evidence |
| `write_guide` | a place, its Wikidata item, sources | one guide revision with claims and evidence |
| `revise` | a revision and the problems found | a new revision |
| `check_claims` | a batch of claims with their passages | verdicts |
| `check_item` | a revision and its claim list | verdict on overstatement and agreement |
| `escalate` | disputed claims with full snapshots | verdicts |
| `audit` | a sample of a run's accepted work | verdicts and the audit result |
| `find_photos` | a place | photo candidates |
| `check_photo` | a photo and its place | verdict |
| `recheck_source` | a source whose text changed | verdicts on its claims |
| `handle_report` | a reader report | a decision |

### 8.2 Leases

`psst task next --type <type> [--city <city>]` atomically leases the next task (`FOR UPDATE SKIP LOCKED`) for the run, for a set time. Two sessions can never get the same task. `psst task submit <task> <result file>` validates the result against the type's schema and the rulebook, applies it through the lifecycle functions, and releases the lease. A lease that expires returns the task to the queue. Every task records its run, model, attempts, and result.

### 8.3 Agent protocol

An agent session needs one instruction: which task types to work on, and for how long. Each task arrives as a file with everything needed (no hunting through briefs); the agent does it and submits. Prompts for each task type live in the repository next to the rulebook, versioned, so every result records which prompt produced it.

### 8.4 Model routing

Each task type has a default model, chosen from measured accuracy and cost (`model_accuracy`). Narrow checks run on the cheapest model that meets the threshold; escalations and audits on a stronger one. The console shows the measured rates so the owner can change routing with evidence.

## 9. The rulebook

One versioned file set in the repository (`rules/`) defines: writing rules (lengths, banned and soft words, spelling, dashes), the identifier and About shapes, story categories and veracity rules, source kinds and what each can support, tag rules, photo rules, and each item type's check plan and threshold. The CLI, the checks, the console, the prompts, and CONTENT_GUIDE all read from it; CONTENT_GUIDE explains it in prose and never restates numbers that live in the rulebook.

Rule changes are reviewed commits with tests. Each revision records the rulebook version it was checked against, so a rule change can recheck exactly the content it affects.

## 10. Places, areas, and coordinates

Kept from the current system, which works well:

- Coordinates only from Wikidata or OpenStreetMap, looked up by the tools, always WGS-84; the app handles GCJ-02 at runtime.
- Areas assigned from real boundary data (Who's On First, OpenStreetMap), never typed.
- Duplicate checks by Wikidata id, OSM element, and similar names within 150 meters.
- The H3 research grid at resolution 7.

Changed:

- One definition of "city" everywhere (the place's city, else its region), used by every filter.
- Area assignment runs after the transaction that creates places, never holding locks during network calls.

## 11. Publishing and the published output

### 11.1 Output

The first output keeps the shape of content format 2 (a manifest, a common pack, one pack per city), so the current app reads it without an update. It adds, as optional fields, what the website and the next app need: per-claim sources for each story and guide, verification dates, and stable item ids. A format 3 with per-area packs and a spatial index comes with the app's redesign, published beside format 2.

### 11.2 Publishing

`psst publish` (or the console's button) takes the publish lock, builds the output from accepted items whose runs passed audit, uploads to staging, downloads it back and checks it (hashes, schemas, references, counts, images served), and promotes only the version it checked. Rollback swaps the manifest back. Every publication records what changed, item by item, so the console can show a diff.

### 11.3 First publish

The first publish from the new platform replaces all current content. Cities appear as they pass acceptance. The old database is archived read-only and kept.

## 12. Admin console

A private SvelteKit app at `/admin/`, server-rendered, reading the database through a read-only role and acting only through the lifecycle functions and the publisher.

Sections:

| section | shows | actions |
| --- | --- | --- |
| Home | Running sessions, what needs the owner (escalations, failed audits, reports, stalled tasks), readiness per city | |
| Cities | Per city: research coverage, items by state, measured accuracy, open tasks | queue research or rewrite tasks |
| Places | Search and filters; a page per place with every item, its revisions, claims, evidence passages, checks, and history | flag, retire, mark a claim wrong |
| Checks | Escalations waiting, audits and their results, measured accuracy per task type and model | decide an escalation, run an audit |
| Tasks | Every task by type and state, leases, attempts | release a stuck lease, cancel, requeue |
| Runs | Every session, its model, output, verdicts, and audit | end a stalled run |
| Publish | What the next publish contains, what's held back and why, checks, versions with diffs | check, publish, roll back |
| Reports, Demand, Map | As today, at scale | |
| Settings | Thresholds, model routing, rulebook version | change thresholds and routing |

Design: one component set shared with the website (section 13), one color scale for states, paged and searchable lists, a page with its own link for every place, item, task, and run, keyboard and screen reader access, dark mode, plain wording.

Sign-in: owner accounts with hashed passwords and sessions, so more people can be added later; every action is logged with the account.

## 13. Website

A public SvelteKit site, prerendered from the published output and served by nginx.

- Pages for every city, neighborhood, place, tag, and (later) tour, each with a stable link and a page title and description search engines can use.
- A map view, a feed-like browse view, and search, in the app's design language.
- The same content as the app, with sources visible and a way to report a problem.
- Accessible (keyboard, screen readers, contrast), fast on slow connections, works without JavaScript for reading.
- English and Simplified Chinese interface, like the app. Content translation stays on the reader's device in the app; the website shows English content.

The website is built after the platform's first publish.

## 14. Security

| role | can |
| --- | --- |
| `psst_owner` | everything, through functions and migrations |
| `psst_agent` | read content; call task and submit functions; nothing else |
| `psst_publisher` | read accepted content; call publish functions |
| `psst_console` | read everything; call owner action functions on behalf of a signed-in account |
| `psst_web` | read published output only (the website never reads the database at runtime if prerendered) |
| `psst_api` | submit reports and demand, as today |

- Functions run with definer rights and record the calling run and account; the client can't choose who it is.
- Secrets live in environment files on the server and the owner's machine, never in the repository.
- The console and its actions require sign-in, a same-site origin, and CSRF protection.
- The tunnel for cloud sessions keeps its token and connection limits, and reads web pages only on the public internet.
- nginx sends HSTS, a content security policy, and no-referrer for the console.
- Backups: a nightly compressed dump plus a split CSV export to the private backup repository, with a weekly restore test against the latest backup.

## 15. Environments, testing, and operations

- **Local:** a development database in Docker with a fixed sample dataset.
- **Test:** every test run creates a fresh database from the migrations and the sample data. No test reads live data.
- **Staging:** a staging database restored from production nightly, for trying migrations and the console before release.
- **Production:** the server, as today.
- **CI:** GitHub Actions on every push: tests, type checks (mypy for Python, svelte-check for the web), linting, and a migration dry run.
- **Deploys:** from tagged commits only, never from a working copy.
- **Migrations:** numbered, reviewed, tested on staging first, additive where possible.
- **Monitoring:** a health page in the console (database, tunnel, API, backups, last publish, link check), and alerts by email for failures.

## 16. Repository layout

```
psst-content/
  rules/              the rulebook (YAML) and its tests
  prompts/            one prompt per task type, versioned
  psst/
    core/             config, database access, HTTP client, ids
    model/            items, revisions, claims, evidence, places, areas, tags
    lifecycle/        Python side of the lifecycle functions
    checks/           tool checks, claim and item check orchestration, audits
    tasks/            task types, leasing, submit handlers
    sources/          fetching, snapshots, archives, link checks
    research/         cells, leads, briefs
    publish/          output builder, staging, acceptance, promotion
    cli/              one module per command group
  db/migrations/      the new schema, from 0001
  web/
    site/             public website (SvelteKit)
    console/          admin console (SvelteKit)
    ui/               shared components and design tokens
  server/             nginx, services, backups, deploy
  tests/
  legacy/             the current system, read-only, until the switch; then deleted
  docs/
```

## 17. Moving from the current system

1. **Leads:** every current place becomes a lead with its Wikidata id, OSM element, names, and coordinates; every current story becomes a lead note on its place (what it claimed, which sources it cited), clearly marked as unverified input. Writers use them as starting points and must find and quote evidence afresh.
2. **Place identity:** when a researched place matches an old place (same Wikidata id or OSM element), it keeps the old `pl_` id, so places saved in the app still resolve. Old `areaId/spotId` ids carry over in `place_identity`.
3. **Reference data:** boundaries, tags, research cells, and demand counts are copied as they are; they are not curated content.
4. **History:** the old database is archived read-only with its full history.
5. **Switch:** the first publish from the new platform replaces the current output (section 11.3).

## 18. Open decisions

1. **Thresholds:** proposed audit thresholds of 1 error in 50 for stories and 1 in 30 for guides and photos.
2. **Model routing defaults:** proposed Haiku 5.5 for claim checks and photo checks, Sonnet 5.5 for writing, escalations, and audits, until measured rates say otherwise.
3. **Order of cities:** proposed London first (largest, best sources), then Hong Kong, Shanghai, Kuala Lumpur.
4. **Console accounts:** proposed one owner account now, with accounts possible later.
5. **Hosting the website:** proposed the current server behind nginx, with a CDN later if traffic needs it.

## 19. Milestones

Each milestone ends with tests passing, a short report, and screenshots where there is anything to see.

| milestone | contents | done when |
| --- | --- | --- |
| M1 Foundation | Repository layout, rulebook, new schema and lifecycle functions, roles, test database, CI | Lifecycle rules proven by tests, including the ones that must be refused |
| M2 Evidence | Fetch and snapshots, claims and evidence, tool checks | A sample story with a wrong year or a missing passage is refused by tools alone |
| M3 Tasks | Task queue and leases, writer and check task types, prompts, claim checks, escalation, audits | Two sessions in parallel never collide; an audit failure reopens a run |
| M4 Publishing | Output builder in format 2, staging, acceptance, promotion, rollback, diffs | A city publishes from the new database to staging and checks out |
| M5 Console | The admin console, sign-in, owner actions | The owner can run the whole workflow from the console |
| M6 Migration | Leads from the current system, place identity, reference data | Every old place and story is a lead; ids carry over |
| M7 Rebuild | Research and writing at scale, city by city | London passes acceptance and goes live |
| M8 Website | The public website | City and place pages live, accessible, indexed |

The iOS app's redesign follows M8 and uses format 3.
