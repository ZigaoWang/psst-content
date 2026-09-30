# Start here

The person running you will say what to do in a few words, for example:

- `seed london`
- `seed hong kong, 5 cells`
- `seed shanghai around the bund, keep going until the Bund is done`
- `seed london deeper` (more passes on the partly done cells with the most left, instead of new cells)
- `photos london` (or `photos london, 20 places`)
- `review`
- `check the old content` (a quick spot check per city)
- `verify london` (check every old story in a city, as cheaply as possible)
- `status`

Work out which task it is and any details they gave (city, how many cells, an area to focus on, anything to avoid), then follow that section below. Where they didn't say, use the defaults. Don't ask questions you can answer yourself; ask only when the request is truly unclear.

Everything here happens through `uv run psst ...` in this folder. Before researching or reviewing, read `CONTENT_GUIDE.md` in full: it's the rulebook, and these steps assume it.

First, always run `uv run psst status`. If it fails, the machine isn't set up (guide, section 3): say so and stop.

In a Claude Code cloud session, the tools reach the database through an HTTPS tunnel (`PSST_TUNNEL_URL`), which is enough to seed and review. Publishing, adding photos, and setting up a new city need SSH access to the server, which cloud sessions don't have; those commands say so. After a cloud review, stop before publishing and report "ready to publish": the person running you publishes from their own machine. Don't commit or push anything; seeding and reviewing need no git.

## Seed a city

Default: 3 cells, one after another, in the city named.

1. `uv run psst city list`. If the city isn't there, set it up: `uv run psst city add "<City>" --country <ISO code>`. If it says the name wasn't found, pick the right one from the close matches it lists (the official name, like "Kuala Lumpur" or "Hong Kong").
2. Start one research run for the session: `uv run psst run start --kind research --model <your model id> --notes "<what you were asked>"`. It prints the run id. Pass it as `--run <id>` to every command that takes one; environment variables may not last from one command to the next.
3. For each cell:
   - Claim one: `uv run psst research claim --city "<City>"`. Claiming prefers cells with the fewest research passes, so after a partial pass you get a different cell. In a dense cell this takes a few minutes (it sweeps Wikipedia and OpenStreetMap), so give the command up to 10 minutes. If they asked to go deeper, add `--deeper`: it picks the partly researched cell with the most leads left. If they named an area ("around the Bund"), find its coordinates (Wikidata, or a place already in Psst via `uv run psst places search`) and add `--near <lat>,<lon>`.
   - Research it exactly as the guide says (section 5): account for every lead in the brief, add what the sweep can't see, write, tag, check, fix, submit. Read every source with `uv run psst fetch <url> --run <run id>` (guide, section 8): it gets past sites that refuse scripts by reading their Internet Archive copy, and records the read. `draft submit` refuses facts citing a source your run never opened.
   - If the cell is too big to do well in what's left of your session, submit what's finished and leave the remaining leads with `"later": true`. Never rush facts to finish a cell.
4. `uv run psst run finish <run id>`.
5. Report per cell: the neighborhoods, places added, facts written, leads skipped, and leads left for later. Don't review or publish your own drafts.

## Review and publish

Default: everything waiting.

1. Start a review run: `uv run psst run start --kind review --model <your model id> --notes "Review"`, and pass the id it prints as `--run <id>` to every command that takes one.
2. Repeat until it returns no facts: `uv run psst review next --out work/review.json`, then review each fact as the guide says (section 12): open every source with `uv run psst fetch <url> --run <run id>` (approving is refused otherwise), check every claim against what the source says, and approve, edit, or reject with notes naming each source you checked. Write `work/decisions.json`, run `uv run psst review apply work/decisions.json --dry-run`, fix anything it reports, then apply.
3. If photos are waiting (`uv run psst images next --out work/images/review.json --run <run id>` lists them), review them too, as the guide says (section 13): look at every one with `uv run psst images show <id> --run <run id>` before deciding.
4. Publish only once every fact and photo in the batch has been checked this way: `uv run psst publish`. Publishing first and fixing later puts mistakes in front of readers. If the staging check fails, report what it said; don't work around it.
5. `uv run psst run finish <run id>`, and report how many facts and photos were approved, edited, and rejected, and what kinds of problems you found.

## Add photos

Default: 10 places, in the city named. The rules are in the guide, section 13.

1. Start a research run, as above.
2. `uv run psst images find --city "<City>" --limit <n>` (or `--place <id>` for one place). It picks places with published stories and no photo, the ones with the most stories first.
3. For each place, read `work/images/<place id>/candidates.md` and open the previews you consider. Pick the best one or two that clearly show the place, or none if nothing does. Write `work/images/draft.json`, run `uv run psst images check work/images/draft.json`, fix what it reports, then `uv run psst images submit work/images/draft.json --run <run id>`.
4. Finish the run, and report how many places got photos and how many had nothing usable.

## Check the old content

A spot check of the facts migrated from the old files, which were never fully checked against their sources. Default: 60 random facts per city.

1. Start a review run, as above.
2. For each city in `uv run psst city list` that has places: `uv run psst review next --verify --sample --limit 60 --city "<City>" --out work/review.json`, then review them exactly like drafts.
3. Publish, finish the run, and report per city how many were wrong and what kind of mistakes you found. If a city looks bad (more than a few wrong), say so: it needs a full check (`--verify` without `--sample`).

## Verify old content

For checking every migrated story in a city, not just a sample. These stories passed the old validator, so most are fine: the job is to catch the ones that aren't, while spending as little as possible on the rest. Default: batches of 40, until the city is done or the session is nearly full. It runs fine in a cloud session.

1. Start a review run, as above.
2. `uv run psst review next --verify --city "<City>" --limit 40 --out work/review.json`.
3. For each story, check its specific claims (names, dates, numbers, "the first", "the only") against its sources with `uv run psst fetch <url> --run <run id> --find "<those words>"`. `--find` shows only the passages mentioning them, which is usually all you need and costs a fraction of the whole page. Search again with other words if the first try misses; read the whole page (no `--find`) only when the passages don't settle it. Several stories often cite the same page: fetch it once and check them all.
4. Decide, and keep notes to one line naming what confirmed each claim:
   - **approve** when every claim holds;
   - **edit** only to fix what's wrong (a date, a number, an overstatement, veracity that should be `legend`). Don't rewrite stories that are right just to improve the style;
   - **reject** when the central claim isn't in any source and can't be fixed from them.
5. Dry-run and apply as in "Review and publish", then take the next batch. In a cloud session, don't publish: report "ready to publish". Otherwise publish once at the end.
6. Finish the run and report: how many checked, approved, edited, rejected, and what went wrong most often. `uv run psst review progress` shows how much of each city is verified.

## Status

Run `uv run psst status`, `uv run psst city list`, `uv run psst research wanted`, and `uv run psst review progress`, and summarize in plain words: what's in the app, what's waiting for review, open reports, and where people have been looking. Everything is also on the admin page, https://psst.zigao.wang/admin/.

## When something goes wrong

Keep going on your own where you safely can:

- **A claim fails because someone else has the cell:** claim again; it picks another.
- **A source won't open:** use `uv run psst fetch <url>`, which falls back to the Internet Archive. If there's no copy either, find another source that says the same thing; if there isn't one, leave the claim out.
- **Wikipedia, Wikidata, or OpenStreetMap are slow or refusing:** the tools retry by themselves. If a lookup keeps failing, wait a minute and try again. If one place's coordinates still can't be found, skip it with the reason "coordinates unavailable" and move on.
- **`draft check` reports errors:** fix the draft and check again. Never change the tools, the rules, or the database to make something pass.
- **Nothing in a cell clears the bar:** submit a draft with no places and every lead skipped with a reason. That's a valid result.
- **You're running out of room in the session:** submit what's finished (leaving the rest for later), finish the run, and report. Unsubmitted work in `work/` is lost to the next session.

Stop and report, rather than improvise, if a command fails in a way these steps don't cover.
