# Psst content guide

This is the handbook for researching, reviewing, and publishing Psst's places and stories. It's written for a Claude Code session, or a person, who has been asked to do one of those jobs. Everything happens through the `psst` command; you never need to read the code. Read the whole guide before you start. The facts are the product, so the bar is high.

## Contents

1. What Psst is for
2. How content flows
3. Setup
4. Runs
5. Researching a cell
6. Choosing places
7. Writing facts
8. Sources
9. Places, names, and coordinates
10. Tags
11. The draft format
12. Reviewing
13. Publishing
14. Fixing published content
15. Regional notes

## 1. What Psst is for

Psst is a friend leaning over to tell you something about the place you're standing in. Not the guidebook paragraph. The thing that makes you look at an ordinary building differently: the roundabout with a second, secret roundabout underneath it, the station named after a pub that closed a century ago, the hotel whose lobby used to be a banana warehouse.

People browse Psst like a feed, and they open it standing in front of things. Every card has to earn the next swipe, and every pin has to be where the thing actually is.

Three rules sit above everything else in this guide:

- **Surprising.** If a friend wouldn't say "wait, really?", it doesn't go in.
- **True.** Every fact is sourced, and anything unproven is labeled as a legend or disputed. Never let a good story pass as a fact.
- **Findable.** Every place is one physical thing someone can walk up to and point at, with a pin from a real source.

## 2. How content flows

All content lives in one PostgreSQL database on the Psst server. The app never reads the database: it downloads files that `psst publish` generates from it.

Every fact moves through four states, and only one of them reaches the app:

| state | meaning |
| --- | --- |
| `draft` | Written by a researcher. This is the only state research can create. |
| `reviewed` | Checked and approved by a separate review run. |
| `published` | In the app. Set by `psst publish`, and only after staging passes its checks. |
| `retired` | Rejected in review, or taken down. Kept forever with the reason, never deleted. |

Every change is recorded against the run that made it, and every state change, edit, and flag goes into the fact's history (`fact_events`). Each fact keeps when it was researched and by which model, which run reviewed it and what they checked, when it was published, and when it was last verified.

The world is split into research cells: H3 hexagons at resolution 7, each about 5 km² (roughly 2.5 km across). Research happens one cell at a time. Places are grouped in the app by city and neighborhood, which the tools assign from real boundary data (Who's On First and OpenStreetMap). Never type an area, district, or neighborhood yourself.

The jobs:

- **Researcher:** claims a cell, researches it, and submits a draft (sections 5 to 11).
- **Reviewer:** a different run, skeptical by default, that approves, edits, or rejects each draft fact and handles problem reports (section 12).
- **Publisher:** runs `psst publish`, which stages everything, checks it, and only then goes live (section 13).

Use a different session for review than for research. If you can, use a different model too.

## 3. Setup

Once per machine:

1. Install [uv](https://docs.astral.sh/uv/), then run `uv sync` in this repository.
2. Make sure `ssh bwh` reaches the Psst server without a password prompt. The tools open their own tunnel to the database through it.
3. Create `~/.config/psst/env` with the database password (ask the owner; never commit it):
   ```
   PSST_DB_PASSWORD=...
   PSST_CONTENT_URL=https://psst.zigao.wang
   ```
4. Check it works: `uv run psst status`.

Every command below is `uv run psst ...`. `uv run psst --help` lists them, and `uv run psst <command> --help` explains each one.

## 4. Runs

Every batch of work is a run: one cell's research, one review session, one tagging pass. Start one before you change anything, and pass its id to each command with `--run`, or export it:

```
export PSST_RUN=$(uv run psst run start --kind research --model claude-opus-5-5 --notes "Coulsdon cell")
...
uv run psst run finish $PSST_RUN
```

`--model` is the model doing the work, exactly as its id reads (`claude-opus-5-5`, `claude-sonnet-5`). Leave it out only when a person does the work. Kinds: `research`, `review`, `tagging`, `verify`, `manual`.

## 5. Researching a cell

1. **Start a research run** (section 4).
2. **Claim a cell.**
   ```
   uv run psst research claim --city London        # the most wanted open cell in London
   uv run psst research claim --cell 87194ac00ffffff
   ```
   With no `--cell`, it picks the open cell app users asked about most, then the one next to the most finished cells, so coverage grows outward. The claim lasts 12 hours. If you give up, `uv run psst research release <cell>`.
3. **Read the brief** in `work/<cell>/brief.md` (and `brief.json`). It has the cell's bounds and neighborhoods, every place already in this cell and the six around it with their facts, and leads from Wikipedia (in English and the local language) and OpenStreetMap, each marked when it's already in Psst. A sweep is a long list of leads, not a list of places: most will be cut.
4. **Add what the sweep can't see.** Heritage and plaque records (Historic England in the UK, the equivalent body elsewhere), local history societies, station and transit histories, pub histories, filming location databases, music history sites. Then sanity check against the obvious: if a visitor would expect a place here, it should be here, unless there's truly nothing surprising to say.
5. **Check nothing is already in Psst.** `uv run psst places search "Cutty Sark"` finds places by name in any language, or by Wikidata id or OSM element. To add facts to an existing place, reference its id in the draft (section 11); never create it again.
6. **Research and cut** (section 6), **write** (sections 7 and 8), **tag** (section 10), and save the draft as `work/<cell>/draft.json` (section 11).
7. **Check the draft** until it has no errors, and read every warning:
   ```
   uv run psst draft check work/<cell>/draft.json
   ```
   It checks the format, every writing rule, sources, tag ids, duplicates of existing places (by Wikidata id, OSM element, and similar names within 150 meters), and looks up every coordinate.
8. **Do your own review** before submitting. For every fact: does the source actually say this (open it)? Is the veracity honest? Would a friend say "wait, really?" Does the short version stand on its own? Does it sound like the fact before it?
9. **Submit:**
   ```
   uv run psst draft submit work/<cell>/draft.json
   ```
   Everything is stored as drafts. New places get their coordinates, license, city, district, and neighborhood automatically, and names in other languages from Wikidata and OpenStreetMap. The cell moves to `drafted`.
10. **Finish the run.**

A cell can come out empty. Suburbs and parks sometimes have nothing that clears the bar. Submit a draft with no places and a `notes` line saying what you checked, so nobody repeats the work. The cell is marked done straight away.

`work/` is a scratch folder and is never committed.

## 6. Choosing places

### What counts as a place

A place is one specific, findable, physical thing with its own pin: a building, a bridge, a station entrance, a statue, a hotel, a roundabout, a staircase, a lamppost, a pub, a bollard, a plaque, a dock wall, a tree, a rock. Someone should be able to walk up to it and point.

Never write one entry for a whole district, neighborhood, estate, or street network. If a street is the place, it must be one short, specific street or alley with its own story, and its pin goes on that street. If a big complex has several good stories, split it into its parts (the station entrance, the clock tower, the gate), each with its own pin.

### What every cell should cover

- **Ordinary places with a secret.** A bus stop, a car park, a chain hotel, a footbridge, a corner shop. These are the heart of the app. At least half of a cell's places should be places a tourist would never look up.
- **Every station.** Each metro, rail, tram, and ferry station is a candidate. Most have a story: where the name came from, a closed platform, an entrance that used to be somewhere else, a design detail everyone walks past.
- **Places people actually go.** Hotels (small and ordinary ones too), pubs, cafes, markets, and old shops.
- **The famous places people come for,** including music, film, TV, and literature spots. Visitors will open the app standing right there, so a missing famous place feels broken. Lead with the detail nobody knows, not the one everyone does.

### Where the good stories hide

- Names that make no sense today: stations, pubs, streets, and alleys named after things long gone.
- Things that were moved, rebuilt, or disguised: a fake house front hiding a railway vent, a church moved stone by stone, a statue that used to stand somewhere else.
- The smallest, oldest, or strangest version of something: a one-room building, the narrowest alley, a door that leads nowhere.
- Street furniture with a past: boundary markers, old signs, police boxes, cabmen's shelters, bollards made from old cannon.
- A building's past life: the hotel that was a warehouse, the bar that was a bank vault.
- People tied to one exact spot: where someone lived, worked, or did the thing they're known for.
- What was recorded, filmed, or written here, and the story behind it.
- Rules and customs that only apply here: odd bylaws, ceremonies, tolls, rents paid in strange things.
- In nature: a rock with a name and a story, a tree older than the town, a shoreline that used to be somewhere else.

### Targets per cell

- Dense city centers: 15 to 40 places.
- Residential neighborhoods and suburbs: 5 to 20.
- Countryside, parks, and quiet edges: as many as genuinely deserve it, including none.

Each place has 1 to 4 facts. One excellent fact is enough for a place to exist. Quality always beats count.

### Leave a place out if

- The best you can say is that it's old, tall, popular, or designed by someone famous.
- The only interesting thing is a generic superlative ("one of the busiest stations in Europe").
- You can't find a solid source for the surprising part.
- Going there would send people somewhere they shouldn't be: private homes, restricted sites, dangerous places. Public exteriors of private buildings are fine.
- It would point at a private living person who isn't a public figure, or at the home of a recent crime victim. Places tied to tragedies are fine when the story is historical and written with respect.

List the good-looking leads you cut, with why, in the draft's `skipped` list. It saves the next researcher the same work.

## 7. Writing facts

A good fact is **specific, surprising, and true.**

- **Specific.** Names, numbers, years, and physical details. "The stones came from the old London Bridge" beats "the stones have an interesting history."
- **Surprising.** It should change how someone sees the place.
- **True.** If you can't back it up, it doesn't go in as a fact.

### Categories

Pick the one that fits best. In the app each category has its own color, and people can filter the map and the feed by category, so choose honestly: a film location story is `pop`, not `history`.

| category | use it for |
| --- | --- |
| `name` | where a name came from, especially odd or misleading names |
| `hidden` | something physically there that people miss: a buried structure, a secret room, a detail on a facade |
| `history` | what used to happen here, what it replaced, who was here |
| `design` | architecture, deliberate design choices, art, signage |
| `engineering` | how it was built, how it works, what holds it up |
| `people` | a person whose story is tied to this exact spot |
| `pop` | music, film, TV, books, and games: what was recorded, filmed, or written here |
| `quirk` | odd rules, strange laws, unusual customs, records, coincidences |

### Veracity: fact, legend, or disputed

Every fact has a `veracity`. This is the most important field.

- `fact`: well documented by reliable sources. You'd bet on it.
- `legend`: a story people tell that's unproven or known to be false. Write it so it's obviously a story ("The story goes that...", "Locals like to say...") and, in the long version, say what the evidence actually shows.
- `disputed`: reliable sources disagree, or the popular version is contested. Explain the disagreement in the long version.

If you're not sure whether something is a fact, it isn't a `fact`. Be especially skeptical of the stories every tour guide tells. A popular story that only appears in one source, or only in sources repeating each other, is a `legend` until an independent reliable source confirms it.

### Headline, short, and long

- `headline`: a few plain words naming the secret, up to 60 characters. Not a pun, not clickbait, no question marks. Example: "A second roundabout underneath".
- `short`: the whisper. One or two sentences, up to 220 characters. It's what shows on the feed card and the map card, so it must stand on its own with no context. Lead with the surprising part.
- `long`: the story for people who want more, 300 to 1,200 characters. Add context, the how and why, names and dates, and, for legends and disputes, what the evidence says. Don't repeat the short version with more adjectives.

Put a place's best fact first. It's the one shown on the feed card. Prefer a plain `fact` as the lead; lead with a legend only when it's clearly the best story, since the card then carries a "Legend" label.

### Voice

Write like a well-read friend talking, not like a brochure or an encyclopedia.

- Plain words, concrete nouns, active verbs.
- Confident but not breathless. No exclamation marks.
- Don't address the reader constantly. An occasional "look up at the corner" is fine when it helps them find the thing.
- Every fact should read like it was written fresh. Watch for phrases you've already used nearby ("look closely", "most visitors walk past", "to this day") and vary or cut them.
- Write in your own words. Never copy sentences from a source. Short quotes are fine only when the exact wording matters, and they go in quotation marks.
- No "did you know", "fun fact", "hidden gem", "psst", or "little-known".
- Avoid words that make writing sound machine-made: "nestled", "boasts", "testament to", "rich tapestry", "vibrant", "delve", "bustling", "iconic", "stands as", "a must-see", "steeped in history", "whispers of the past", "not just X, but Y". The checks reject the worst of these.

Good:

> **headline:** A second roundabout underneath
> **short:** The roundabout outside the station sits on top of another one. Delivery trucks circle the lower level so they never have to stop on the street.

Bad:

> **short:** This iconic roundabout boasts a fascinating hidden history that most people never notice!

A legend done right:

> **headline:** A site picked by counting passersby
> **short:** The story goes that the founders posted a man on each side of the road to count passersby, and built on the side that won.

### Language and spelling

- Facts are written in US English, always. Spelling and punctuation too ("color", "center", "theater", "meter", "gray"). The checks catch common British spellings. The app translates stories on the reader's device when they ask; never write or store translations.
- Proper names keep their own spelling: "Southbank Centre" and "National Theatre" stay as they are. (Only lowercase words are checked, so capitalized names are safe.)
- Never use em dashes or en dashes. Use a period, a comma, a colon, parentheses, or the word "to" for ranges ("1840 to 1852").
- Use metric units, with imperial in parentheses where a local reader would expect it ("62 meters (202 feet)" for a London monument built to exactly 202 feet).
- Write names from other languages the way English speakers see them on the ground, and put the local script in `localName`.

## 8. Sources

Every fact needs at least one real source with a working `https` URL, and at least one source per fact must be something other than Wikipedia. Wikipedia is great for leads, but cite what it cites.

Roughly in order of preference:

1. Official listings and records: national heritage lists, parliaments, surveys, city and national archives, official gazetteers.
2. The owner or operator: the transit authority, the building's own history page, the church, the company.
3. Museums, universities, and academic publications.
4. Reputable newspapers and magazines, and long-running specialist sites with a track record.

Useful regional sources:

- **London:** Historic England, Survey of London (British History Online), London Remembers, Londonist, Ian Visits, London Historians. Historic England and Londonist often refuse scripts; British Listed Buildings republishes the Historic England list text, and an archived copy of a Londonist page is fine. Cite the original URL when you've read it through a copy.
- **Shanghai and mainland China:** the local gazetteers (上海地方志, shtong.gov.cn, including the district 区志 and specialist 专志 volumes), municipal and district government sites, city archives, The Paper (澎湃), SHINE, Sixth Tone. The old shtong.gov.cn pages mostly survive only as `web.archive.org` copies; cite those.
- **Kuala Lumpur and Malaysia:** The Star, New Straits Times, Malay Mail, Badan Warisan Malaysia, and Malay and Chinese language papers.
- **Anywhere else:** the national heritage body, the city archive, and the leading local newspaper, in the local language.

Rules:

- Outside English speaking places, don't rely only on English sources. Read the local language sources, which usually have far more detail about individual buildings and streets, then cite the primary source.
- Link to the live original page where it exists. An archived copy is fine when the original is gone or unreachable.
- Never cite a search results page or an AI answer. The checks reject them.
- Avoid content farms, AI-written listicles, and travel sites that don't cite anything.
- For legends, cite a source that tells the story and, ideally, one that examines it.
- Open every source and confirm it actually says what the fact claims.

Sources are stored once and shared: cite the same page from two facts and it's one source linked twice. Give it the same title and publisher each time.

## 9. Places, names, and coordinates

### Coordinates

You never write coordinates. Each new place names its Wikidata item (`"wikidata": "Q935104"`), its OpenStreetMap element (`"osm": "way/40778038"`), or both, and the tools look up the position when you check and submit. Always WGS-84, including in China; the app handles China's shifted maps itself.

- **Wikidata first.** It's CC0, so it needs no attribution. The item's coordinate (P625) is used when there's exactly one and it's precise to about 50 meters.
- **OpenStreetMap otherwise.** A node's position, or the middle of a way's or relation's bounding box. OSM data is ODbL, and the app credits it. If you give both and Wikidata's coordinate is missing, doubled, or too coarse, OSM is used automatically.
- **Check it makes sense.** The Wikidata point for a large thing (a park, a long bridge) is sometimes far from where people stand. If it's clearly wrong for what you describe, give only the OSM element. If neither source has the place, leave it out.
- **Put the pin where people will stand.** For a small detail on a big building (a plaque, a doorway, a carving), use the OSM node for that detail if one exists. If it doesn't, use the building and say where to look in the fact.

To find an item: `uv run psst tags wikidata "Cutty Sark"` searches Wikidata (it works for places too), and the brief lists the OSM elements in the cell. For anything else, search OpenStreetMap: `[out:json];nwr["name"~"Cutty Sark"](51.47,-0.02,51.49,0.0);out center;` at `https://overpass-api.de/api/interpreter`.

A place must fall in the claimed cell or one of its six neighbors. A place in a neighboring cell is fine (you'll get a warning); it belongs to the cell its coordinate is in.

### Names

- `name`: the name people use on the ground, in English where one exists. When a place has no common English name, use the romanized local name people would actually see (pinyin for mainland China, the Malay name in Malaysia) rather than inventing a translation.
- `localName`: the name on the signs when it differs from the English one, with its language: `{"lang": "zh-Hans", "name": "和平饭店"}`. Always fill it in when the local name differs, so people can match the pin to the sign in front of them.
- Names in other languages (so a search for 大本钟 finds Big Ben) come from Wikidata labels and OSM name tags automatically. Don't add them.

### Kinds and sizes

| kind | for |
| --- | --- |
| `transit` | stations, stops, piers, depots, anything you board |
| `crossing` | bridges, tunnels, footbridges, subways |
| `street` | streets, alleys, roundabouts, junctions, steps, street furniture |
| `building` | offices, homes, hotels, shops, pubs, banks, towers |
| `worship` | churches, temples, mosques, synagogues, shrines |
| `memorial` | statues, monuments, plaques, markers, boundary stones |
| `green` | parks, gardens, squares, cemeteries, trees, and natural features like hills, rocks, cliffs, and beaches |
| `water` | docks, basins, rivers, canals, lakes, fountains, wells |
| `culture` | museums, theaters, galleries, studios, venues, markets, stadiums |

`size` is `small` (a statue, a door, a bollard), `medium` (a building, a station, a square; the default), or `large` (a skyscraper, a long bridge, a park, a hill). Small places are pictured with Apple's Look Around street view where it exists; medium and large ones get a 3D or satellite view, framed further back for large ones.

## 10. Tags

Tags are the threads that connect places: a person or group, an event, an era, a movement, or a theme. In the app, tapping one shows every place it connects. A tag appears in the app only once it connects at least three places.

| type | examples |
| --- | --- |
| `person_or_group` | Charles Dickens, The Beatles, East India Company |
| `event` | The Blitz, Great Fire of London, Festival of Britain |
| `era` | Roman Britain, Swinging Sixties |
| `movement` | Art Deco, Brutalism, Women's suffrage |
| `theme` | Lost rivers, Ghost stations, Pubs, Film locations |

Rules:

- **Reuse first.** Look before you add: `uv run psst tags search "beatles"` matches names and aliases. `uv run psst tags list` shows everything, most used first.
- **Adding a tag:** find its Wikidata item (`uv run psst tags wikidata "Festival of Britain"`), then
  ```
  uv run psst tags propose "Festival of Britain" --type event --wikidata Q1316963 --alias "1951 Festival"
  ```
  It prints the tag id. If the name, an alias, or the Wikidata item matches an existing tag, you get that tag instead of a new one. If it's merely similar, nothing is created and you're shown the candidates: use one, or, if yours really is a different thing, repeat with `--distinct-from <id>` for each. Themes often have no Wikidata item; that's fine.
- **Canonical names** are the common English name with correct spelling and accents ("Simón Bolívar", not "Simon Bolivar"). Other spellings go in as `--alias`.
- **Tag what the fact is about,** not everything it mentions. Two or three tags is typical; more than four is almost always too many. A fact with no real thread gets no tags.
- **Only threads that could connect several places.** "Pubs" and "Charles Dickens" will; "This one bus stop" won't.
- Keep the vocabulary clean: `uv run psst tags audit` lists near-duplicate pairs, and `uv run psst tags merge <from> <into>` folds one into another (all its facts move over, and its name becomes an alias). Merging needs a `tagging` run; after merging, `psst publish --no-new-facts` updates the app.

## 11. The draft format

A draft is one JSON file for one cell. The schema is `format/draft.schema.json`; `psst draft check` enforces it.

```json
{
  "cell": "87194ad14ffffff",
  "notes": "Covered the river frontage and the stations. The estate to the south had nothing that held up.",
  "places": [
    {
      "name": "Greenwich Foot Tunnel",
      "kind": "crossing",
      "size": "medium",
      "wikidata": "Q935104",
      "osm": "way/40778038",
      "facts": [
        {
          "category": "history",
          "veracity": "fact",
          "headline": "Built so dockers could get to work",
          "short": "One or two sentences.",
          "long": "The longer story, 300 to 1,200 characters.",
          "sources": [
            { "url": "https://www.royalgreenwich.gov.uk/...", "title": "Greenwich Foot Tunnel", "publisher": "Royal Borough of Greenwich" }
          ],
          "tags": ["tg_k13twy63"]
        }
      ]
    },
    {
      "place": "pl_kdmsj9y7c8",
      "facts": [ { "category": "quirk", "veracity": "legend", "...": "..." } ]
    }
  ],
  "skipped": [
    { "name": "Greenwich Market", "reason": "Everything interesting is already covered by the existing place." }
  ]
}
```

| field | rules |
| --- | --- |
| `cell` | The cell you claimed. |
| `notes` | Optional. What you covered and anything the reviewer or the next researcher should know. |
| `places` | New places, and new facts for existing places. |
| `places[].place` | For facts about a place already in Psst: its id (`pl_...`). Nothing else about the place goes in. |
| `places[].name`, `localName`, `kind`, `size` | For a new place. See section 9. |
| `places[].wikidata`, `osm` | For a new place: at least one. If you give both, they must be the same thing. |
| `places[].facts` | 1 to 4 facts, best first. Each has `category`, `veracity`, `headline`, `short`, `long`, `sources` (each with `url`, `title`, `publisher`), and `tags` (tag ids; `[]` for none). |
| `skipped` | Optional. Leads you looked at and left out, with `name`, `reason`, and optionally `wikidata` or `osm`. |

Ids are assigned by the tools and never change. Nothing else exists in the format; unknown fields are rejected, so a typo can't slip through.

## 12. Reviewing

Review is where Psst stays trustworthy. Assume every draft has a mistake in it until you've failed to find one.

1. Start a review run: `export PSST_RUN=$(uv run psst run start --kind review --model <model>)`. A run can never review its own research.
2. Get a batch: `uv run psst review next --out work/review.json` (add `--cell <cell>` for one cell, `--limit` for more than 25). Reported facts come first, then drafts. Each item has the fact, its place and pin, its sources and tags, the place's other facts, and any open problem reports.
3. For every fact, check:
   - **The source says it.** Open every source. Check each number, name, and date against it. A claim the sources don't make is a rejection, or an edit that removes it.
   - **The veracity is honest.** One source, or sources repeating each other, means `legend` at most. Would a skeptical historian sign off on `fact`?
   - **It's surprising.** Would a friend say "wait, really?" If not, reject it.
   - **The place is right.** The pin (`lat`, `lon`, `location`) is on the thing described, and it's one physical thing, not an area.
   - **The writing** follows section 7: the short version stands alone, nothing machine-sounding, no repeats of the place's other facts.
   - **The tags** fit and aren't padded.
   - **Reports:** read what the reader said and check it properly. They're often right.
4. Write your decisions to a file, one per fact:
   ```json
   [
     { "fact": "fa_3k9x2m4q7p", "decision": "approve",
       "notes": "Checked the 1902 date and the architect against the Historic England listing." },
     { "fact": "fa_8w2n5v1c0r", "decision": "edit",
       "notes": "The listing says 1898, not 1902. Fixed the year in short and long.",
       "changes": { "short": "...", "long": "..." } },
     { "fact": "fa_1q7z4t9y2e", "decision": "reject", "reason": "Neither source mentions the tunnel.",
       "notes": "Read both sources in full; the tunnel story appears only on a tour company blog." }
   ]
   ```
   - `notes` is required on every decision and says what you checked, in a sentence or more. It's kept in the fact's history.
   - `edit` can change `category`, `veracity`, `headline`, `short`, `long`, `sources` (the full new list), and `tags` (the full new list), and approves the result. Edits are checked by the same rules as drafts.
   - `reject` needs a `reason`. The fact is retired, never deleted.
5. Check, then apply. Nothing is written if any decision has a problem:
   ```
   uv run psst review apply work/decisions.json --dry-run
   uv run psst review apply work/decisions.json
   ```
   Approved and edited drafts become `reviewed`. Reported facts that were already published stay published (with your edits) and are marked verified. Any open reports on a fact are resolved with your decision and notes.

`uv run psst reports list` shows open problem reports from the app. They're also in `review next`.

## 13. Publishing

```
uv run psst publish
```

This one command:

1. Exports everything published, plus every reviewed fact, into content format 2 (a manifest and one pack per city; `format/v2/`). The export is deterministic: the same database always gives byte-identical files.
2. Re-checks every exported fact against the writing rules and every pack against its schema.
3. Uploads to **staging** and downloads it back exactly as the app would, checking every hash, every reference (places to areas, facts to tags), every old place id, and that the number of places and facts hasn't dropped by more than 2 percent.
4. Only if all of that passes, promotes staging to **production** by switching one file atomically. Reviewed facts become `published`, and finished cells become `done`.

If any check fails, production is untouched and the problems are listed. Apps keep the last good version they have, and they never switch to a download that doesn't check out.

- `--only-staging`: stop after checking staging.
- `--allow-shrink "reason"`: allow a drop of more than 2 percent (for example after retiring a batch). The reason is recorded.
- `--no-new-facts`: re-export what's already published, for example to publish a tag merge.
- `uv run psst rollback` points production back at the previous version (or `--to <version>`); `uv run psst prune` deletes pack files no recent version needs.
- `uv run psst bundle` copies what production serves into the app repository (`../psst-map/Content/v2`) as the snapshot the app ships with. Do it before an app release, then rebuild the app.
- `uv run psst coverage` rebuilds the coverage map at `/coverage/` on the Psst site (user `psst`; the password is in `/www/wwwroot/psst/coverage.password` on the server). It shows every cell by state, with place and fact counts, and where app users asked for places.

## 14. Fixing published content

- **Something is wrong in a published fact:** `uv run psst review flag <fact id> --reason "..."`. It stays live until a review run approves, edits, or rejects it (section 12). Readers' problem reports from the app do the same automatically.
- **Adding to a place:** research its cell again (`psst research claim --cell <cell>` works on finished cells too) and reference the place by id in the draft.
- **A place is gone or nothing about it holds up:** reject all its facts in review. A place with no published facts disappears from the app, but its id is never reused, so saved places don't break.
- **Never edit the database by hand.** Every change goes through a command, so it's attributed to a run and kept in the history.

## 15. Regional notes

### Mainland China

- Everything is stored in WGS-84, like everywhere else. Apple Maps draws mainland China in GCJ-02 only when it's using its China map provider, which depends on where the device is. The app checks this at runtime and shifts pins only when needed.
- Apple has no 3D buildings for Chinese cities, so the app shows these places from straight above. Nothing in the content needs to change for this.
- Wikipedia, Wikimedia, and many Western sites are blocked in mainland China, and some Chinese government sites are hard to reach from outside it. Neither affects the app, which downloads content from the Psst server, but prefer sources a reader in either place can open.
- District and neighborhood boundaries in Shanghai come from OpenStreetMap, where they're far more complete than Who's On First. For a new Chinese city, load its OSM boundaries first (the owner does this; see `docs/DESIGN.md`).

### Places with other languages and scripts

- Always fill in `localName` when the local name differs from the English one.
- Search and read in the local language (the brief already includes the local Wikipedia). The best stories about ordinary places are almost never in English.

### Being a good API citizen

- Requests carry a plain User-Agent, `PsstContent/1.0`. Never put an email address or other personal information in a request.
- Wikidata, Wikipedia, and Overpass are shared services. The tools retry and fall back on their own (the Wikidata query service when the API refuses, the main OSM API when Overpass is busy). Don't run more than a few researchers at once.
