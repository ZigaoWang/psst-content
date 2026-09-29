# Instructions for Claude Code and other agents

This repository is the Psst content pipeline: the `psst` command, the database schema, the app content format, and the server setup. The content itself (places and stories) lives in the database on the Psst server, never in files here. The facts are the product.

## Before you start

- Read `CONTENT_GUIDE.md` in full. It's the spec for researching, reviewing, and publishing, and it explains every command you need. You shouldn't need to read the code.
- Check your setup with `uv run psst status` (section 3 of the guide).

## Rules

- Every change goes through a `psst` command under a run (`psst run start`), so it's attributed and kept in history. Never write SQL against the content tables, and never edit the database by hand.
- Research only ever creates drafts. Review happens in a different run, never the one that did the research. Only `psst publish` puts anything in front of users, and only through staging.
- Every place is one physical thing with its own pin. Coordinates come only from Wikidata or OpenStreetMap, looked up by the tools. Never type, guess, or convert coordinates, and never assign areas or neighborhoods yourself.
- Every fact is sourced, with at least one non-Wikipedia source you actually opened, and honestly marked `fact`, `legend`, or `disputed`.
- Writing: US English, no em dashes or en dashes, no exclamation marks, no marketing or machine-sounding language. Your own words, never copied from a source.
- Reuse tags before proposing new ones, and let `psst tags propose` catch duplicates.
- Never put an email address, name, or other personal information in any request (User-Agent included). Use `PsstContent/1.0`.
- Don't loosen a check to make content pass. Fix the content. Changes to the rules or tools are separate, deliberate commits with tests.
- Keep scratch work in `work/` (ignored by git). Never commit drafts, briefs, decisions, or other one-off files.

## Changing the code

- `uv run pytest` must pass. The database tests run inside transactions that are always rolled back.
- Schema changes are new files in `db/migrations/`, never edits to applied ones. Apply with `uv run psst db migrate`.
- Content format changes are app changes too: agree them with the app (`../psst-map`) first. Within format 2, only add optional fields; anything incompatible is format 3, published beside format 2.
- After changing server code, `sh server/deploy.sh`.
- Commits: Conventional Commits without a scope (`feat: ...`, `fix: ...`, `docs: ...`, `test: ...`; `style:` only for formatting). One small change per commit. No co-author lines.
