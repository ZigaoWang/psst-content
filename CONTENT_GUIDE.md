# Psst content guide

This is the handbook for adding places to Psst. It's written for a Claude Code session, or a person, who has been asked to research an area and add it. It works for any kind of place: a dense city center, a quiet residential neighborhood, a small town, a stretch of coastline, a university campus. Read the whole guide before you start. The facts are the product, so the bar is high.

## Contents

1. What Psst is for
2. The workflow
3. Planning areas
4. Finding spots
5. Choosing spots
6. Writing facts
7. Sources
8. Coordinates
9. The file format
10. Editing existing areas
11. Checking your work
12. Committing and publishing
13. Regional notes

## 1. What Psst is for

Psst is a friend leaning over to tell you something about the place you're standing in. Not the guidebook paragraph. The thing that makes you look at an ordinary building differently: the roundabout with a second, secret roundabout underneath it, the station named after a pub that closed a century ago, the hotel whose lobby used to be a banana warehouse.

People browse Psst like a feed, and they open it standing in front of things. Every card has to earn the next swipe, and every pin has to be where the thing actually is.

Three rules sit above everything else in this guide:

- **Surprising.** If a friend wouldn't say "wait, really?", it doesn't go in.
- **True.** Every fact is sourced, and anything unproven is labeled as a legend or disputed. Never let a good story pass as a fact.
- **Findable.** Every spot is one physical thing someone can walk up to and point at, with a pin from a real source.

## 2. The workflow

1. **Plan.** Define the area (or, for a whole city or region, the full set of areas) and write the plan down. See section 3.
2. **Sweep.** Collect a long list of candidate spots with `scripts/sweep.py`, not from memory. See section 4.
3. **Research and cut.** Research each candidate. Keep only the ones with something genuinely surprising to say. See section 5.
4. **Locate.** Get every coordinate from Wikidata or OpenStreetMap with `scripts/coords.py`. See section 8.
5. **Write.** Write each fact as a headline, a short version, and a long version, with sources and an honest status. See sections 6 and 7.
6. **Save.** Save the area as `areas/<area-id>.json` in this repository and run `python3 scripts/format.py`.
7. **Check.** Run the validator, fix everything, then do the human review. See section 11.
8. **Commit and publish.** One commit per area, then publish into the app. See section 12.

The tools, all run from the repository root:

| command | what it does |
| --- | --- |
| `python3 scripts/sweep.py --bounds S,W,N,E --name <area-id> [--lang zh]` | collects candidates from OpenStreetMap and Wikipedia into `candidates/` (not committed) |
| `python3 scripts/coords.py Q123 way/456` | prints exact coordinates, ready to paste, and refuses imprecise ones |
| `python3 scripts/format.py` | rewrites area files in the one canonical format |
| `python3 scripts/validate.py [--online]` | checks every rule in this guide that a script can check |
| `python3 scripts/stats.py [prefix]` | places, facts, categories, and statuses per area |
| `python3 scripts/publish.py` | validates everything, then copies the areas into the app |

## 3. Planning areas

An area is how research is split up, and how the app groups and frames places on the map. It's never a spot itself.

### Sizing an area

- **Dense city centers:** roughly 1 to 2 km across. There's more than enough in that space.
- **Residential neighborhoods and suburbs:** 2 to 4 km across.
- **Small towns and villages:** usually the whole town is one area.
- **Countryside, coastlines, and parks:** follow the natural unit (a valley, a stretch of coast, a national park section), up to about 15 km across. The validator warns above 15 km and refuses anything over 20 km.

Follow the boundaries locals actually use. An area called "Soho" should feel like Soho to someone who lives there.

### Naming an area

- `id`: `place-area` in lowercase kebab case, like `london-greenwich`, `shanghai-the-bund`, or `cornwall-st-ives`. It can never change once published.
- `name`: the everyday name of the area.
- `city`: the city the area belongs to. For places outside a city, use the nearest town or the region people would name (`Cornwall`, `Lake District`). Areas with the same `city` are grouped together in the app, so spell it the same way every time (`London`, never `Greater London`).

### Planning a whole city or region

1. Write the plan to `plans/<city>.md` before researching anything: every area id, its name, its bounding box, and a status column (`planned`, `in progress`, `done`). It lets work continue across sessions and lets parallel researchers see who owns what. Plans are working notes for a seeding run, so `plans/` is ignored by git; the areas themselves are the record.
2. Make sure the boxes cover everything with no gaps between neighbors. Small overlaps at the edges are fine; section 10 explains who owns a border spot.
3. Go beyond the tourist center. Residential neighborhoods, outer districts, markets, and industrial edges often have the best ordinary place stories.
4. Research areas in parallel if you can, but check for overlap before adding any spot near a border. Keep parallel work to a handful of areas at a time: Wikidata and Overpass are shared services and will start refusing requests.
5. Validate and commit each area as it's finished and update its status in the plan, then do a final check across all of them for duplicates and gaps.

## 4. Finding spots

Don't rely on what you already know about an area. That's how famous places get missed and ordinary ones never get found. Sweep first, collect a long list, then research and cut. A good sweep usually produces three to five times more candidates than end up in the file.

Start with the sweep script:

```
python3 scripts/sweep.py --bounds 51.468,-0.025,51.490,0.010 --name london-greenwich
python3 scripts/sweep.py --area london-greenwich                       # an existing area's bounds
python3 scripts/sweep.py --bounds ... --name shanghai-jingan --lang zh  # add a local language Wikipedia
```

It runs the OpenStreetMap query below over the area, searches Wikipedia across a grid of points so nothing falls between the circles, merges the two, and marks candidates that are already in an area file. The results land in `candidates/<name>.md`, with Wikidata ids and OSM elements where known. Outside English speaking places, always add the local language with `--lang` (`zh`, `ms`, `ja`, and so on): local wikis cover buildings the English one ignores.

For reference, the OpenStreetMap part of the sweep is:

```
[out:json][timeout:180][bbox:S,W,N,E];
(
  nwr[historic]; nwr[heritage]; nwr[memorial];
  nwr[tourism~"attraction|museum|artwork|viewpoint|hotel|gallery"];
  nwr[railway=station]; nwr[public_transport=station]; nwr[amenity=ferry_terminal];
  nwr[amenity~"pub|bar|cafe|restaurant|theatre|cinema|place_of_worship|marketplace"][wikidata];
  nwr[amenity=pub];
  nwr[shop][wikidata]; nwr[building][wikidata]; nwr[man_made][wikidata];
  nwr[natural][wikidata]; nwr[bridge][name];
);
out center tags;
```

Then add what the sweep can't see:

- **Heritage and plaque records.** National and local heritage lists (Historic England in the UK, the equivalent body elsewhere), blue plaques and their local versions, and local history societies.
- **Specialist sources.** Station and transit histories, pub histories, filming location databases, music history sites, and long-running local history sites. Use these as leads. They can be sources too if they meet the bar in section 7.

Then sanity check the list against the obvious. If a visitor to this area would expect to find a place in the app, it should be there, unless there's truly nothing surprising to say about it.

## 5. Choosing spots

### What counts as a spot

A spot is one specific, findable, physical thing with its own pin: a building, a bridge, a station entrance, a statue, a hotel, a roundabout, a staircase, a lamppost, a pub, a bollard, a plaque, a dock wall, a tree, a rock. Someone should be able to walk up to it and point.

Never write one entry for a whole district, neighborhood, estate, or street network. If a street is the spot, it must be one short, specific street or alley with its own story, and its pin goes on that street. If a big complex has several good stories, split it into its parts (the station entrance, the clock tower, the gate), each with its own pin. The validator warns about names that sound like areas.

### What every area should cover

- **Ordinary places with a secret.** A bus stop, a car park, a chain hotel, a footbridge, a corner shop. These are the heart of the app. At least half of an area's spots should be places a tourist would never look up.
- **Every station.** Each metro, rail, tram, and ferry station inside the bounds is a candidate. Most have a story: where the name came from, a closed platform, an entrance that used to be somewhere else, a design detail everyone walks past.
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

### Targets

- Dense city centers: 30 to 60 spots.
- Neighborhoods and towns: 20 to 40 spots.
- Countryside and small places: as many as genuinely deserve it, even if that's 10.

Each spot has 1 to 4 facts. One excellent fact is enough for a spot to exist. Quality always beats count: 20 great spots are better than 40 thin ones.

### Leave a spot out if

- The best you can say is that it's old, tall, popular, or designed by someone famous.
- The only interesting thing is a generic superlative ("one of the busiest stations in Europe").
- You can't find a solid source for the surprising part.
- Going there would send people somewhere they shouldn't be: private homes, restricted sites, dangerous places. Public exteriors of private buildings are fine.
- It would point at a private living person who isn't a public figure, or at the home of a recent crime victim. Places tied to tragedies are fine when the story is historical and written with respect.

## 6. Writing facts

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

### Status: fact, legend, or disputed

Every fact has a `status`. This is the most important field in the file.

- `fact`: well documented by reliable sources. You'd bet on it.
- `legend`: a story people tell that's unproven or known to be false. Write it so it's obviously a story ("The story goes that...", "Locals like to say...") and, in the long version, say what the evidence actually shows.
- `disputed`: reliable sources disagree, or the popular version is contested. Explain the disagreement in the long version.

If you're not sure whether something is a fact, it isn't a `fact`. Be especially skeptical of the stories every tour guide tells. A popular story that only appears in one source, or only in sources repeating each other, is a `legend` until an independent reliable source confirms it.

### Headline, short, and long

- `headline`: a few plain words naming the secret, up to 60 characters. Not a pun, not clickbait, no question marks. Example: "A second roundabout underneath".
- `short`: the whisper. One or two sentences, up to 220 characters. It's what shows on the feed card and the map card, so it must stand on its own with no context. Lead with the surprising part.
- `long`: the story for people who want more, 300 to 1,200 characters. Add context, the how and why, names and dates, and, for legends and disputes, what the evidence says. Don't repeat the short version with more adjectives.

Put a spot's best fact first. It's the one shown on the feed card. Prefer a plain `fact` as the lead; lead with a legend only when it's clearly the best story, since the card then carries a "Legend" label.

### Voice

Write like a well-read friend talking, not like a brochure or an encyclopedia.

- Plain words, concrete nouns, active verbs.
- Confident but not breathless. No exclamation marks.
- Don't address the reader constantly. An occasional "look up at the corner" is fine when it helps them find the thing.
- Every fact should read like it was written fresh. Watch for phrases you've already used in the same area ("look closely", "most visitors walk past", "to this day") and vary or cut them. The validator warns when a stock phrase shows up in more than two facts of an area.
- Write in your own words. Never copy sentences from a source. Short quotes are fine only when the exact wording matters, and they go in quotation marks.
- No "did you know", "fun fact", "hidden gem", "psst", or "little-known".
- Avoid words that make writing sound machine-made: "nestled", "boasts", "testament to", "rich tapestry", "vibrant", "delve", "bustling", "iconic", "stands as", "a must-see", "steeped in history", "whispers of the past", "not just X, but Y". The validator rejects the worst of these.

Good:

> **headline:** A second roundabout underneath
> **short:** The roundabout outside the station sits on top of another one. Delivery trucks circle the lower level so they never have to stop on the street.

Bad:

> **short:** This iconic roundabout boasts a fascinating hidden history that most people never notice!

A legend done right:

> **headline:** A site picked by counting passersby
> **short:** The story goes that the founders posted a man on each side of the road to count passersby, and built on the side that won.

### Language and spelling

- US English spelling and punctuation everywhere ("color", "center", "theater", "meter", "gray"). The validator catches common British spellings.
- Proper names keep their own spelling: "Southbank Centre" and "National Theatre" stay as they are. (The validator only checks lowercase words, so capitalized names are safe.)
- Never use em dashes or en dashes. Use a period, a comma, a colon, parentheses, or the word "to" for ranges ("1840 to 1852").
- Use metric units, with imperial in parentheses where a local reader would expect it ("62 meters (202 feet)" for a London monument built to exactly 202 feet).
- Write names from other languages the way English speakers see them on the ground, and put the local script in `localName`.

## 7. Sources

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
- Never cite a search results page or an AI answer. The validator rejects them.
- Avoid content farms, AI-written listicles, and travel sites that don't cite anything.
- For legends, cite a source that tells the story and, ideally, one that examines it.
- Open every source and confirm it actually says what the fact claims.
- `python3 scripts/validate.py --check-links` requests every URL and lists the ones that fail. Some sites refuse scripts; those are ignored.

## 8. Coordinates

Coordinates must come from a real source. Never estimate from memory, from a map you're looking at, or from a street address.

The easy way: find the Wikidata item or OSM element, then

```
python3 scripts/coords.py Q935104 way/40778038
```

prints the `coordinate` and `coordinateSource` to paste into the spot, using exactly the lookups the validator uses. It refuses Wikidata coordinates that are too imprecise.

The rules behind it:

1. **Wikidata first.** If the spot has a Wikidata item with a coordinate (property P625), use it. To find an item: `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=NAME&language=en&format=json`.
2. **OpenStreetMap otherwise.** Find the node, way, or relation. For a node its position is used; for a way or relation, the center that Overpass computes (the middle of its bounding box). To search by name inside an area: `[out:json];nwr["name"~"Cutty Sark"](51.47,-0.02,51.49,0.0);out center;`
3. **Check the precision.** If a Wikidata coordinate has fewer than 4 decimal places, it can be 100 meters out. Use the OSM element instead.
4. **Check it makes sense.** The Wikidata point for a large thing (a park, a long bridge) is sometimes far from where people stand. If it's clearly wrong for what the spot describes, use the OSM element. If neither source has the spot, leave the spot out.
5. **Put the pin where people will stand.** For a small detail on a big building (a plaque, a doorway, a carving), find the OSM node for that detail if one exists. If it doesn't, use the building and say where to look in the fact.

Copy the numbers exactly as the source gives them. Don't round them or add digits. `validate.py --online` re-fetches every coordinate and fails anything more than 30 meters from its source.

**Always store WGS-84, everywhere, including China.** Wikidata and OpenStreetMap both use it. The app works out at runtime whether Apple Maps is drawing China in the shifted GCJ-02 system and converts if so (see section 13). Never convert or "fix" coordinates by hand in the data files.

### Being a good API citizen

- Use a plain User-Agent like `PsstContent/1.0`. Never put an email address or other personal information in a request.
- Overpass is shared and rate limited. On a 429 or 504, wait a few seconds and retry. Batch lookups where you can: `(way(1);way(2);node(3););out center;`.
- If Overpass is down, the main OSM API works for single elements: `https://api.openstreetmap.org/api/0.6/node/123.json`, or `.../way/123/full.json` for a way, where the coordinate to use is the middle of the bounding box of its nodes.
- If the Wikidata API returns 429, the query service at `https://query.wikidata.org/sparql` is limited separately. The tools fall back to both automatically.

## 9. The file format

One JSON file per area in `areas/`, named after the area `id` plus `.json`. UTF-8, two-space indentation, non-ASCII characters written as-is. `scripts/format.py` produces exactly this, and the validator rejects anything else.

```json
{
  "schemaVersion": 1,
  "id": "london-greenwich",
  "name": "Greenwich",
  "city": "London",
  "countryCode": "GB",
  "summary": "Ships, stars, and the line the whole world sets its clocks by.",
  "researchedOn": "2026-09-28",
  "bounds": { "south": 51.4680, "west": -0.0250, "north": 51.4900, "east": 0.0100 },
  "spots": [
    {
      "id": "greenwich-foot-tunnel",
      "name": "Greenwich Foot Tunnel",
      "localName": null,
      "kind": "crossing",
      "size": "medium",
      "coordinate": { "latitude": 51.4833, "longitude": -0.0102 },
      "coordinateSource": { "type": "wikidata", "id": "Q935104" },
      "facts": [
        {
          "id": "built-for-dockers",
          "category": "history",
          "status": "fact",
          "headline": "Built so dockers could get to work",
          "short": "One or two sentences.",
          "long": "The longer story.",
          "sources": [
            { "title": "Greenwich Foot Tunnel", "publisher": "Royal Borough of Greenwich", "url": "https://www.royalgreenwich.gov.uk/..." }
          ]
        }
      ]
    }
  ]
}
```

Only the fields below exist. The validator rejects unknown fields, so a typo can't slip through. There's no field for photos yet.

### Area fields

| field | rules |
| --- | --- |
| `schemaVersion` | Always `1` for now. |
| `id` | `place-area` in lowercase kebab case. Must match the filename. Never change it once published. |
| `name` | The area's everyday name. |
| `city` | The city, or the nearest town or region for places outside cities. Areas with the same `city` are grouped together. |
| `countryCode` | ISO 3166-1 alpha-2, like `GB`, `MY`, `CN`. The app uses it to decide between a 3D view and a straight-down satellite view. |
| `summary` | One sentence, up to 120 characters, in the app's voice. Shown in the area picker. |
| `researchedOn` | The date of the most recent research, `YYYY-MM-DD`. |
| `bounds` | A tight box around the area in WGS-84. Every spot must be inside it. The app uses it to frame the area on the map. |
| `spots` | The list of spots. Order doesn't matter; the app sorts and shuffles. |

### Spot fields

| field | rules |
| --- | --- |
| `id` | Lowercase kebab case, unique within the area. Saved spots are stored as `areaId/spotId`, so never change or reuse an id once published. |
| `name` | The name people use on the ground, in English where one exists. |
| `localName` | The name in the local script or language if it differs, like `和平饭店` for the Peace Hotel. Otherwise `null` or omitted. The validator warns when it's missing in countries with non-Latin scripts. |
| `kind` | One of the kinds below. It sets the pin color and icon, and the kind filter. |
| `size` | `small` (a statue, a door, a bollard), `medium` (a building, a station, a square), or `large` (a skyscraper, a long bridge, a park, a hill). Defaults to `medium`. Small spots are pictured with Apple's Look Around street view where it exists; medium and large spots get a 3D or satellite view, framed further back for large ones. |
| `coordinate` | `latitude` and `longitude` in WGS-84, copied from `coordinateSource`. |
| `coordinateSource` | `{ "type": "wikidata", "id": "Q..." }` or `{ "type": "osm", "id": "node/123" }` (also `way/123` or `relation/123`). |
| `facts` | At least one. The best one goes first. |

### Kinds

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

### Fact fields

| field | rules |
| --- | --- |
| `id` | Lowercase kebab case, unique within the spot. Never change it once published. |
| `category` | One of `name`, `hidden`, `history`, `design`, `engineering`, `people`, `pop`, `quirk`. |
| `status` | `fact`, `legend`, or `disputed`. |
| `headline` | Up to 60 characters. |
| `short` | Up to 220 characters, one or two sentences. |
| `long` | 300 to 1,200 characters. |
| `sources` | At least one. Each has `title`, `publisher`, and an `https` `url`. At least one per fact must not be Wikipedia. |

### Changing the format

New categories, kinds, and fields are app changes too, so they're agreed with the app first. The app is built to cope with content that's newer than it: an unknown category or kind is shown in a neutral style, and a fact or spot it can't read is skipped rather than breaking its area. Even so, add them to the validator and this guide in the same commit, and never repurpose an existing value.

## 10. Editing existing areas

- **Search first.** Before adding a spot, check it isn't already in any area. The sweep marks candidates that are, and `grep -ril "cutty sark" areas/` finds names. A spot belongs to exactly one area; the validator fails two spots with the same coordinate source anywhere in the repository.
- **Border spots.** A spot near a border goes to whichever area already has it. If neither does, it goes to the area whose bounds contain the coordinate. If both do, pick the area where people would say it is.
- **Adding to an area.** Add new spots and facts, update `researchedOn`, and validate the whole repository.
- **Fixing a fact.** Fix it in place and keep its `id`. If a fact turns out to be wrong, correct it or change its status. Remove it only if there's nothing true left to say.
- **Removing a spot.** Only when it no longer exists or nothing about it holds up. Never reuse its id for something else.
- **Changing bounds.** You can widen bounds to add spots. Don't shrink them in a way that leaves existing spots outside.

## 11. Checking your work

Run these from the repository root:

```
python3 scripts/format.py                # canonical formatting
python3 scripts/validate.py              # structure, rules, and style, across every area
python3 scripts/validate.py --online     # also re-fetches every coordinate from Wikidata or OSM
python3 scripts/stats.py london          # a summary to review against the targets
```

The validator fails on anything that would break the app or the rules above: missing or unknown fields, bad ids, duplicates across areas, spots outside their bounds, oversized bounds, text that is too long or too short, em dashes, British spellings, banned phrases, missing or Wikipedia-only sources, formatting, and coordinates that don't match their source. It warns about softer problems: repeated stock phrases, missing local names, spots that sound like whole areas, a legend leading a spot, and spots very close to each other. Fix every error and read every warning.

Then do the human review, because the validator can't tell whether something is true or interesting. For every fact:

1. Does the source actually say this? Open it and check.
2. Is the status honest? Would a skeptical reader call this a legend?
3. Would a friend say "wait, really?" If not, cut it.
4. Does the short version make sense on its own?
5. Does it read fresh, or does it sound like the fact before it?

For the area as a whole:

1. Is at least half of it ordinary places, not landmarks?
2. Are the stations, hotels, pubs, and famous places people come for covered?
3. Would someone who lives there recognize their neighborhood in it?

Finally, publish (section 12), build and run the app, and look at a sample of spots on the map and in the feed. Check that pins sit on the right building.

## 12. Committing and publishing

### Committing

This repository uses Conventional Commits without a scope, one small change per commit:

- A new area: `feat: add Soho`.
- More places in an existing area: `feat: add 8 places to Greenwich`.
- A correction: `fix: correct the Monument's height`, `fix: mark the Horse Guards clock story as a legend`.
- Guide changes are `docs:`; tooling is `feat:` or `fix:`; formatting-only changes are `style:`.

Rules:

- One area per commit. Never mix content with tooling or guide changes.
- Commit only files that pass `format.py --check` and `validate.py`. Run `sh scripts/install-hooks.sh` once after cloning, and a pre-commit hook enforces this for you.
- Never commit one-off working files: sweep results, plans, notes, or scratch scripts. `candidates/`, `plans/`, and `tmp/` are ignored for that; anything else goes in `/tmp`.
- No co-author lines in commit messages.

### Publishing

```
python3 scripts/publish.py              # into ../psst-map, or set PSST_APP_DIR, or pass --app
python3 scripts/publish.py --online     # re-check coordinates first
```

The app bundles whatever is in its `Content/areas` folder. Publishing validates every area and then syncs that folder: new and changed files are copied and removed areas are deleted. If anything fails validation, nothing is copied. Rebuild the app to see the result. The app's own unit tests also load every published file.

## 13. Regional notes

### Mainland China

- Data files always store WGS-84, like everywhere else. Apple Maps draws mainland China in GCJ-02 only when it's using its China map provider, which in practice depends on where the device is. The app checks this at runtime and shifts pins only when needed.
- Apple has no 3D buildings for Chinese cities, so the app shows these places from straight above. Nothing in the content needs to change for this.
- Wikipedia, Wikimedia, and many Western sites are blocked in mainland China, and some Chinese government sites are hard to reach from outside it. Neither affects the app, which bundles its content, but prefer sources a reader in either place can open.

### Places with other languages and scripts

- Always fill in `localName` when the local name differs from the English one, so people can match the pin to the sign in front of them.
- Search and read in the local language (and sweep with `--lang`). The best stories about ordinary places are almost never in English.
- When a place has no common English name, use the romanized local name people would actually see (pinyin for mainland China, the Malay name in Malaysia) rather than inventing a translation.
