# Psst content platform: redesign plan

Status: approved direction, October 9, 2026. The detailed specification is [design.md](design.md).

## Why

The data and its curation are the whole value of Psst. The current platform grew in layers: JSON area files, then a database, then photos copying the fact workflow, then guides copying it again. It works, but it's hard to keep consistent, hard to audit, and expensive to verify. Migrated content still carries verification debt. Patching each problem leaves the same shape, so the platform is rebuilt deliberately, designed first and built once.

## Standard

Every part (schema, pipeline, agent workflows, admin console, published output, website, apps, docs, wording) is held to the same bar: professional, robust, consistent, safe, user friendly, and plainly written. One mechanism per job, no parallel copies, no leftovers.

## Approach

1. **Design document first.** Data model, workflows, task system, console, published output, security. Concrete (tables, states, screens, examples), short enough to read in one sitting, approved by the owner before any code.
2. **Build beside the old system.** New schema, pipeline, and console run against a copy while the old system keeps serving the app.
3. **Rebuild the content.** The existing places and stories become research leads only; every published fact is researched and checked again under the new rules. Place ids carry over so saved places still work.
4. **Switch over in one step.** The first publish from the new platform replaces the current content, and the app shows only new, checked content from then on. The old database is archived read-only, and the old code is deleted.

## Design decisions

- **Revisions, never overwrites.** Places, stories, guides, and photos each keep an immutable chain of revisions with author, sources, and reviewer. Any past state can be read back, and undoing a review is a normal operation. This replaces the current event triggers, reopen logic, and per-table history.
- **Provenance per claim.** Each source on a story states which claims it supports. Verification means every claim has a checked source, which tools can track and reviewers can't skip.
- **Verification as a recorded status.** Every published item records when it was last verified, against which sources, and by which run. There is no hidden "migrated, never checked" category.
- **One lifecycle for every content type,** enforced by the database: draft, in review, approved, published, retired. A new content type (tours, audio) is configuration, not another copy of the workflow.
- **Work as a task queue.** Research a cell, write guides, review, verify, find photos: every job is a task with a lease, inputs, outputs, and a quality record. Sessions request the next task and never collide. Spot-check audits of any run (for example a cheaper model's run checked by a stronger one) are built in.
- **Review levels by content type.** Stories: every claim checked against its sources. Guides: one source and every flagged value. Photos: looked at. An authoritative record (a heritage listing, an official archive, the Survey of London) supports a plain fact on its own; the one-source rule for legends applies to anecdotes, not records.
- **Stable ids and versioned output.** Places, stories, and sources keep permanent ids. Published output carries a format version and only grows in compatible ways.
- **Languages from the start.** Names already come in several languages; the model leaves room for translated guide text without a schema change.
- **Rules in one place.** Writing rules, review levels, and source requirements live in one rulebook read by the pipeline, the console, and the docs.
- **Least privilege.** Cloud sessions change content only through database functions that enforce the lifecycle. History is written by the database, attributed to the run, and can't be edited.
- **Tests on a throwaway database** built from the migrations and fixed sample content. Nothing touches live data. Tests run on every push.
- **Backups that scale:** a compressed dump plus a split export, with a weekly restore test against the real latest backup.

## Accuracy mechanism

No model guarantees accuracy; the October 9 spot check found 10 to 17 percent of a cheaper model's guide approvals wrong, and the errors were misreadings, not invented sources. Accuracy has to come from the structure of the work, not from paying for a stronger model.

1. **Evidence-bound claims.** Every claim (a year, name, number, event) carries the exact passage from its source that supports it. Sources are saved as text with a hash when first read, and the tools check that each quoted passage appears in the saved text. Invented or misremembered quotes fail automatically.
2. **Traceable details.** Every year, number, and proper name in an identifier, About, or story must appear in one of its quoted passages, or the check refuses it.
3. **Narrow review.** Reviewers see each claim beside its passage and answer one question: does this passage say this? The reviewer is asked to find the mismatch, not to approve.
4. **Independent double checks.** Two separate cheap checks per claim with different instructions. Agreement passes; disagreement goes to a stronger model or the owner. Expensive models see only the hard cases.
5. **Measured acceptance.** Every run is audited on a random sample by a stronger model. Its work publishes only when the measured error rate is under the owner's threshold (for example 1 in 50); otherwise it's reopened automatically. The console shows each model's measured accuracy per task, so each job uses the cheapest model that meets the bar.
6. **Structured facts first.** Years, makers, materials, and heritage status are fields with evidence; identifiers and key facts are checked against them, and Wikidata values that conflict with a passage are flagged.
7. **Closed loop.** Reader reports, the owner's spot checks, and the weekly link check feed one queue. A source that changes or disappears sends its claims back for checking against the saved copy.

## Published output

One output serves the iOS app and the website: a public read API plus static data files, versioned and checked on staging before going live, as today.

## Admin console

A private web app replacing the single-file admin page, held to the same bar as the public site.

- **Data:** small summary files per city and per place; nothing loads the whole database. Lists are paged and searchable.
- **Sections:** Home (what's running, what needs attention, readiness per city), Cities, Places (a full page per place with its own link), Review (queues, per-run quality, spot-check audits), Publish (what goes live, what's held back, checks, publish, rollback, logs, version diffs), Sessions, Reports, Demand, Map, Settings.
- **Actions:** publish, check, roll back, refresh, flag and unflag, close a stalled run. Authenticated, same-site only, carried out by the server, logged. Work that needs judgment stays in agent sessions; the console shows the prompt to start one.
- **Quality:** one component set, one color scale, keyboard and screen reader access, dark mode, small screens, plain wording.

## Website

A public site built on the published output, not a replacement for the iOS app: every city, neighborhood, place, story, tag, and tour has its own page and link, with a map, the app's design language, full accessibility, and pages search engines can read. Built after the platform.

## iOS app

Revised after the platform and website. From the October 8 audit:

- Split `AppModel` into a content store, a filter state that keeps the visible places, and a router; give the feed and search their own models; split the place page into sections.
- A design system: spacing and radius tokens, shared card, icon tile, close button, and translate control.
- Scale: pins for the visible area only, an area index for nearby lookups, cities loaded on demand, a real search index. Needs a compatible addition to the content format.
- Small on-device stores for "seen", Look Around results, and translations instead of whole-file rewrites.
- Accessibility (Look Around and Share in the feed, the map card's close button, the photo viewer), catalog plurals, a plain copy pass.
- Mocked-network tests for content updates; UI tests on fixed sample content in their own scheme.

## Order

| step | work | sessions |
| --- | --- | --- |
| 1 | Design document, approved by the owner | 1 |
| 2 | Schema, lifecycle, revisions, provenance, task queue, test database | 2 to 3 |
| 3 | Pipeline commands and agent workflows | 2 to 3 |
| 4 | Admin console | 2 |
| 5 | Migration with verification tracking, switch over, delete old code | 1 to 2 |
| 6 | Website | 2 to 3 |
| 7 | iOS app revision | 2 to 3 |

## Decisions

Made on October 9, 2026: existing content is used as research leads only; the app shows only new, checked content once the new platform publishes; SvelteKit for the website and console; the design lives in this repository. Remaining open decisions are in [design.md](design.md), section 18.

## Already done (October 8, 2026)

Security and correctness fixes in both repositories, committed and pushed:

- Admin buttons work only from the admin page; publishing is locked and promotes only the checked version; claims and review decisions can't collide; the server's page reader stays on the public web; clean CLI errors; run indexes.
- App: safe file names and links from the server, the area signal sends nothing without a known location and converts China coordinates, reports send once, search no longer sticks, the feed keeps its place, first launch can download content, plain translated load errors, dark mode loading screens, window sizes instead of `UIScreen.main`.

## State of content work on October 8, 2026

All agents stopped at the owner's request; everything finished is stored in the database, nothing published.

- **Guide writing** (`run_5wgzsccf5w`, open): London 1,932 of 2,081 places drafted. A prepared batch of 40 (from St Peter upon Cornhill) is unwritten in `work/guides/run_5wgzsccf5w/draft.json` and claimed for 12 hours. Left: about 150 London places, Shanghai, Hong Kong, and the small towns. Kuala Lumpur is done.
- **Guide review** (`run_eehp5bexxg`, open): 400 reviewed (60 approved, 340 edited, 0 rejected). About 2,290 waiting. Common problems: identifiers in the wrong shape, About claims not in the sources, Wikidata values conflicting with sources. About 35 story claims that conflict with sources are listed in `work/guides/story_notes.md` for a story review.
- **Story review** (`run_c1dpp9nx7b`, open): all 664 waiting stories decided (about 70 approved, 400 edited, 150 rejected). A single-source audit reopened 110 stories (100 back in draft, 10 published and unverified); its second pass was stopped before applying anything. The list is in `work/audit_flag.json` and can be rebuilt from `psst review next`.
- **London verification** (`run_cceqw64xaf`, closed): about 1,110 of 2,594 verified; about 1,500 left. Common mistakes: details not in the cited source, overstated claims, dates that don't match the source, conflicting sources.
- **Rule to settle before the next story pass:** the single-source rule was applied to official records as well as anecdotes, which labels documented facts as legends. Recommended: an authoritative record supports a plain fact on its own; the rule applies to anecdotes.
