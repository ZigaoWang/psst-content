# Psst content

The places and stories behind [Psst](../psst-map), a map of the surprising things about specific places. Each area is one JSON file in `areas/`. The app bundles them; there is no server.

This repository is separate from the app because the content is its own work, with its own scope and license (see `LICENSE`).

## Getting started

```
sh scripts/install-hooks.sh     # once: blocks commits with invalid area files
python3 scripts/validate.py     # check everything
python3 scripts/stats.py        # what's here
```

Everything needs only Python 3.10 or later, with no packages to install.

## Adding content

Read [CONTENT_GUIDE.md](CONTENT_GUIDE.md) first. It covers what makes a good spot and a good fact, how to find them, the file format, and how to check the work. The short version:

```
python3 scripts/sweep.py --bounds S,W,N,E --name london-soho   # collect candidates
python3 scripts/coords.py Q123 way/456                          # exact coordinates
python3 scripts/format.py                                       # canonical formatting
python3 scripts/validate.py --online                            # check, including coordinates
git commit -m "feat: add Soho"                                  # one area per commit
python3 scripts/publish.py                                      # copy into the app
```


## Layout

| path | what |
| --- | --- |
| `areas/` | one file per area, named after its id |
| `scripts/` | the validator and the tools listed in the guide |
| `candidates/`, `plans/`, `tmp/` | working files for a seeding run, not committed |

## Publishing to the app

`python3 scripts/publish.py` validates every area and syncs `areas/` into the app's `Content/areas` folder (default `../psst-map`, or set `PSST_APP_DIR`). If anything fails validation, nothing is copied. Then build the app.

## Compatibility

The app reads `schemaVersion` 1. It shows content from newer formats as best it can: unknown categories and kinds get a neutral style, and entries it can't read are skipped instead of breaking the area. Format changes are agreed with the app first; see the guide, section 9.
