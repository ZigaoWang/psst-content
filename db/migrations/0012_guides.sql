SET search_path = psst, public;

-- Practical guide information for a place, kept apart from its stories: a one-line identifier ("Bronze
-- statue, 1843, by Edward Baily"), a short neutral About, its sources, and key facts from Wikidata. A guide
-- goes through the same lifecycle as a fact, with a lighter review. A place has at most one guide waiting
-- for review; publishing a new one retires the one it replaces.
CREATE TABLE guides (
    id               text PRIMARY KEY CHECK (id ~ '^gd_[0-9a-hjkmnp-tv-z]{10}$'),
    place_id         text NOT NULL REFERENCES places,
    identifier       text NOT NULL CHECK (char_length(identifier) BETWEEN 3 AND 70),
    about            text NOT NULL CHECK (char_length(about) BETWEEN 100 AND 700),
    -- The item the key facts were read from, when the place has one.
    wikidata_id      text CHECK (wikidata_id ~ '^Q[1-9][0-9]*$'),
    state            text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'reviewed', 'published', 'retired')),
    researched_at    date NOT NULL,
    researched_by    text NOT NULL,
    research_run     text NOT NULL REFERENCES pipeline_runs,
    reviewed_at      timestamptz,
    reviewed_by      text,
    review_run       text REFERENCES pipeline_runs,
    review_notes     text,
    published_at     timestamptz,
    last_verified_at timestamptz,
    retired_at       timestamptz,
    retire_reason    text,
    needs_review     boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (state NOT IN ('reviewed', 'published') OR reviewed_at IS NOT NULL),
    CHECK (state <> 'published' OR published_at IS NOT NULL),
    CHECK (state <> 'retired' OR (retired_at IS NOT NULL AND retire_reason IS NOT NULL))
);
CREATE INDEX guides_place_idx ON guides (place_id);
CREATE INDEX guides_state_idx ON guides (state);
CREATE UNIQUE INDEX guides_one_waiting ON guides (place_id) WHERE state IN ('draft', 'reviewed');
CREATE TRIGGER guides_touch BEFORE UPDATE ON guides FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE guide_sources (
    guide_id  text NOT NULL REFERENCES guides ON DELETE CASCADE,
    source_id text NOT NULL REFERENCES sources,
    position  integer NOT NULL,
    PRIMARY KEY (guide_id, source_id)
);
CREATE INDEX guide_sources_source_idx ON guide_sources (source_id);

-- One row per value, read from Wikidata by the tools and never typed. `property` is the Wikidata property
-- it came from (P170 creator, P571 inception, ...). `flag` says why a sanity check found it implausible; a
-- review must confirm a flagged value or drop it before the guide can be approved.
CREATE TABLE guide_key_facts (
    guide_id       text NOT NULL REFERENCES guides ON DELETE CASCADE,
    position       integer NOT NULL,
    property       text NOT NULL CHECK (property ~ '^P[1-9][0-9]*$'),
    label          text NOT NULL,
    value          text NOT NULL CHECK (value <> ''),
    value_id       text CHECK (value_id ~ '^Q[1-9][0-9]*$'),
    flag           text,
    flag_confirmed boolean NOT NULL DEFAULT false,
    PRIMARY KEY (guide_id, position)
);

-- Every lifecycle change and edit of a guide, kept forever, like fact_events.
CREATE TABLE guide_events (
    id         bigserial PRIMARY KEY,
    guide_id   text NOT NULL REFERENCES guides ON DELETE CASCADE,
    at         timestamptz NOT NULL DEFAULT now(),
    from_state text,
    to_state   text NOT NULL,
    changes    text[] NOT NULL DEFAULT '{}',
    actor      text NOT NULL,
    run_id     text,
    note       text
);
CREATE INDEX guide_events_guide_idx ON guide_events (guide_id, at);

CREATE FUNCTION psst.record_guide_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    changed text[] := '{}';
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.identifier IS DISTINCT FROM OLD.identifier THEN changed := array_append(changed, 'identifier'); END IF;
        IF NEW.about IS DISTINCT FROM OLD.about THEN changed := array_append(changed, 'about'); END IF;
        IF NEW.needs_review IS DISTINCT FROM OLD.needs_review THEN
            changed := array_append(changed, CASE WHEN NEW.needs_review THEN 'flagged' ELSE 'unflagged' END);
        END IF;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.state IS DISTINCT FROM OLD.state OR cardinality(changed) > 0 THEN
        INSERT INTO psst.guide_events (guide_id, from_state, to_state, changes, actor, run_id, note)
        VALUES (NEW.id, CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.state END, NEW.state, changed,
                coalesce(nullif(current_setting('psst.actor', true), ''), current_user),
                nullif(current_setting('psst.run', true), ''),
                nullif(current_setting('psst.note', true), ''));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guides_events AFTER INSERT OR UPDATE ON guides FOR EACH ROW EXECUTE FUNCTION psst.record_guide_event();

-- Places a research run is writing guides for, so two sessions never write the same one. Claims expire.
CREATE TABLE guide_claims (
    place_id      text PRIMARY KEY REFERENCES places,
    run_id        text NOT NULL REFERENCES pipeline_runs,
    claimed_until timestamptz NOT NULL
);
