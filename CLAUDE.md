# Instructions for Claude Code and other agents

This repository holds the Psst content: the places and their stories. The facts are the product.

## Before you start

- Read `CONTENT_GUIDE.md` in full. It is the spec. Follow it exactly.
- Run `sh scripts/install-hooks.sh` once, so invalid files can't be committed.
- If you're seeding a whole city, write or update its plan in `plans/<city>.md` first and keep each area's status current, so the work can be resumed by another session. Plans, sweeps, and notes are working files: they stay out of git.

## Rules

- Every spot is one physical thing with its own pin. Never a whole area.
- Coordinates come only from Wikidata or OpenStreetMap, through `scripts/coords.py`. Never guess, estimate, or convert them. Always WGS-84, including in China.
- Every fact is sourced, with at least one non-Wikipedia source you actually opened, and honestly marked `fact`, `legend`, or `disputed`.
- Writing: US English, no em dashes or en dashes, no exclamation marks, no marketing or machine-sounding language. Your own words, never copied from a source.
- Never put an email address, name, or other personal information in any request (User-Agent included). Use `PsstContent/1.0`.
- Don't edit the validator or tools to make content pass. Fix the content. Tool changes are separate, deliberate commits.
- Never edit the app repository directly. Content reaches it only through `scripts/publish.py`.

## Checking and committing

1. `python3 scripts/format.py`
2. `python3 scripts/validate.py --online` must report 0 errors. Read every warning.
3. Do the human review in the guide, section 11.
4. Commit one area at a time, Conventional Commits without a scope: `feat: add Soho`, `feat: add 8 places to Greenwich`, `fix: correct the Monument's height`. No co-author lines.
5. `python3 scripts/publish.py` to sync into the app.

## Working in parallel

- Parallel researchers each own whole areas, listed in the plan. Nobody edits another researcher's area file.
- Keep it to a handful of areas at a time. Wikidata and Overpass rate limit shared IP addresses; the tools retry and fall back, but hammering them makes everything slower.
- Before committing, run the validator across the whole repository: it catches the same place added to two areas.
