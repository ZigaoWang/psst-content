-- Every lead found for a research cell (Wikipedia articles and OpenStreetMap features inside it), and what
-- happened to it. A draft can only be submitted when every lead is accounted for: added as a place, already
-- in Psst, or skipped with a reason. That's what stops a cell being called done after a light pass.
CREATE TABLE research_leads (
    cell      text NOT NULL REFERENCES research_cells ON DELETE CASCADE,
    key       text NOT NULL,
    name      text NOT NULL,
    wikidata  text,
    osm       text,
    url       text,
    what      text,
    status    text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'added', 'known', 'skipped')),
    reason    text,
    run_id    text REFERENCES pipeline_runs,
    found_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cell, key)
);

-- How many full research passes a cell has had under research by cell.
ALTER TABLE research_cells ADD COLUMN passes integer NOT NULL DEFAULT 0;

-- Cells that only held places from the old area files were marked done without ever being researched as a
-- whole. They go back to open so they get a full pass; their places show up in the brief as already there.
UPDATE research_cells SET state = 'open', last_researched_at = NULL,
       notes = 'Has places from the earlier area research, which never covered the whole cell; needs a full pass.'
WHERE state = 'done' AND passes = 0;
