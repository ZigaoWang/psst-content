-- The Psst content database. See docs/DESIGN.md for what each table is for.
-- Text columns with a fixed set of values use CHECK constraints instead of enum types, so adding a value
-- later is a one-line migration.

CREATE SCHEMA IF NOT EXISTS psst;
SET search_path = psst, public;

CREATE FUNCTION psst.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- Who or what changed content, and as part of which job.
CREATE TABLE pipeline_runs (
    id          text PRIMARY KEY CHECK (id ~ '^run_[0-9a-z]{10}$'),
    kind        text NOT NULL CHECK (kind IN ('import', 'research', 'review', 'tagging', 'names', 'verify', 'publish', 'manual')),
    model       text,                -- model id, or null when a person did the work
    operator    text NOT NULL,       -- the person who started the run
    cell        text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    notes       text
);

-- Boundaries from Who's On First (ids as published) or OpenStreetMap (negative relation ids).
CREATE TABLE admin_areas (
    id           bigint PRIMARY KEY,
    source       text NOT NULL CHECK (source IN ('wof', 'osm')),
    placetype    text NOT NULL,
    level        text NOT NULL CHECK (level IN ('country', 'region', 'city', 'district', 'neighborhood')),
    name         text NOT NULL,
    country_code text,
    parent_id    bigint,
    geom         geometry(Geometry, 4326) NOT NULL,
    is_point     boolean GENERATED ALWAYS AS (GeometryType(geom) = 'POINT') STORED,
    area_km2     double precision,
    license      text NOT NULL
);
CREATE INDEX admin_areas_geom_idx ON admin_areas USING gist (geom);
CREATE INDEX admin_areas_level_idx ON admin_areas (level, country_code);

CREATE TABLE admin_area_names (
    area_id bigint NOT NULL REFERENCES admin_areas ON DELETE CASCADE,
    lang    text NOT NULL,
    name    text NOT NULL,
    PRIMARY KEY (area_id, lang)
);

CREATE TABLE places (
    id               text PRIMARY KEY CHECK (id ~ '^pl_[0-9a-hjkmnp-tv-z]{10}$'),
    kind             text NOT NULL CHECK (kind IN ('transit', 'crossing', 'street', 'building', 'worship', 'memorial', 'green', 'water', 'culture')),
    size             text NOT NULL DEFAULT 'medium' CHECK (size IN ('small', 'medium', 'large')),
    geom             geometry(Point, 4326) NOT NULL,
    coord_source     text NOT NULL CHECK (coord_source IN ('wikidata', 'osm')),
    coord_source_ref text NOT NULL,
    coord_license    text NOT NULL CHECK (coord_license IN ('CC0-1.0', 'ODbL-1.0')),
    wikidata_id      text UNIQUE CHECK (wikidata_id ~ '^Q[1-9][0-9]*$'),
    osm_ref          text UNIQUE CHECK (osm_ref ~ '^(node|way|relation)/[1-9][0-9]*$'),
    h3_cell          text NOT NULL,
    country_code     text,
    region_id        bigint REFERENCES admin_areas,
    city_id          bigint REFERENCES admin_areas,
    district_id      bigint REFERENCES admin_areas,
    neighborhood_id  bigint REFERENCES admin_areas,
    admin_assignment jsonb NOT NULL DEFAULT '{}',
    state            text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'retired')),
    merged_into      text REFERENCES places,
    created_by_run   text NOT NULL REFERENCES pipeline_runs,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (coord_source <> 'wikidata' OR coord_source_ref = wikidata_id),
    CHECK (coord_source <> 'osm' OR coord_source_ref = osm_ref),
    CHECK ((coord_source = 'wikidata') = (coord_license = 'CC0-1.0'))
);
CREATE INDEX places_geom_idx ON places USING gist (geom);
CREATE INDEX places_cell_idx ON places (h3_cell);
CREATE INDEX places_city_idx ON places (city_id);
CREATE TRIGGER places_touch BEFORE UPDATE ON places FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Names: 'display' is the English name the app shows, 'local' is what the signs say, 'alt' are other
-- established names. They come from Wikidata labels, OpenStreetMap name tags, or research.
CREATE TABLE place_names (
    place_id text NOT NULL REFERENCES places ON DELETE CASCADE,
    role     text NOT NULL CHECK (role IN ('display', 'local', 'alt')),
    lang     text NOT NULL,
    name     text NOT NULL CHECK (name <> ''),
    source   text NOT NULL CHECK (source IN ('wikidata', 'osm', 'research')),
    PRIMARY KEY (place_id, role, lang)
);
CREATE UNIQUE INDEX place_names_one_display ON place_names (place_id) WHERE role = 'display';
CREATE UNIQUE INDEX place_names_one_local ON place_names (place_id) WHERE role = 'local';
CREATE INDEX place_names_trgm_idx ON place_names USING gin (lower(name) gin_trgm_ops);

-- Old "areaId/spotId" ids from the area files, so saved places keep working.
CREATE TABLE legacy_place_ids (
    legacy_id text PRIMARY KEY,
    place_id  text NOT NULL REFERENCES places
);

CREATE TABLE sources (
    id                text PRIMARY KEY CHECK (id ~ '^so_[0-9a-hjkmnp-tv-z]{10}$'),
    url               text NOT NULL CHECK (url ~ '^https://'),
    url_key           text NOT NULL UNIQUE,
    title             text NOT NULL,
    publisher         text NOT NULL,
    language          text,
    archived_url      text,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_checked_at   timestamptz,
    last_check_status text
);

CREATE TABLE facts (
    id               text PRIMARY KEY CHECK (id ~ '^fa_[0-9a-hjkmnp-tv-z]{10}$'),
    place_id         text NOT NULL REFERENCES places,
    position         integer NOT NULL,
    category         text NOT NULL CHECK (category IN ('name', 'hidden', 'history', 'design', 'engineering', 'people', 'pop', 'quirk')),
    veracity         text NOT NULL CHECK (veracity IN ('fact', 'legend', 'disputed')),
    headline         text NOT NULL CHECK (char_length(headline) BETWEEN 1 AND 60),
    short            text NOT NULL CHECK (char_length(short) BETWEEN 1 AND 220),
    long             text NOT NULL CHECK (char_length(long) BETWEEN 300 AND 1200),
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
    legacy_id        text UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (state NOT IN ('reviewed', 'published') OR reviewed_at IS NOT NULL),
    CHECK (state <> 'published' OR published_at IS NOT NULL),
    CHECK (state <> 'retired' OR (retired_at IS NOT NULL AND retire_reason IS NOT NULL))
);
CREATE INDEX facts_place_idx ON facts (place_id, position);
CREATE INDEX facts_state_idx ON facts (state);
CREATE INDEX facts_review_idx ON facts (needs_review) WHERE needs_review;
CREATE TRIGGER facts_touch BEFORE UPDATE ON facts FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE fact_sources (
    fact_id   text NOT NULL REFERENCES facts ON DELETE CASCADE,
    source_id text NOT NULL REFERENCES sources,
    position  integer NOT NULL,
    PRIMARY KEY (fact_id, source_id)
);
CREATE INDEX fact_sources_source_idx ON fact_sources (source_id);

-- Every lifecycle change, kept forever. Filled by a trigger so nothing can skip it.
CREATE TABLE fact_events (
    id         bigserial PRIMARY KEY,
    fact_id    text NOT NULL REFERENCES facts ON DELETE CASCADE,
    at         timestamptz NOT NULL DEFAULT now(),
    from_state text,
    to_state   text NOT NULL,
    actor      text NOT NULL,
    run_id     text,
    note       text
);
CREATE INDEX fact_events_fact_idx ON fact_events (fact_id, at);

CREATE FUNCTION psst.record_fact_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' OR NEW.state IS DISTINCT FROM OLD.state THEN
        INSERT INTO psst.fact_events (fact_id, from_state, to_state, actor, run_id, note)
        VALUES (NEW.id,
                CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.state END,
                NEW.state,
                coalesce(nullif(current_setting('psst.actor', true), ''), current_user),
                nullif(current_setting('psst.run', true), ''),
                nullif(current_setting('psst.note', true), ''));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER facts_events AFTER INSERT OR UPDATE ON facts FOR EACH ROW EXECUTE FUNCTION record_fact_event();

CREATE TABLE tags (
    id             text PRIMARY KEY CHECK (id ~ '^tg_[0-9a-hjkmnp-tv-z]{8}$'),
    canonical_name text NOT NULL,
    type           text NOT NULL CHECK (type IN ('person_or_group', 'event', 'era', 'theme', 'movement')),
    wikidata_id    text UNIQUE CHECK (wikidata_id ~ '^Q[1-9][0-9]*$'),
    description    text,
    created_by_run text REFERENCES pipeline_runs,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Every way of writing a tag, canonical name included, normalized. The primary key is what makes
-- "Beatles" and "The Beatles" impossible to add twice.
CREATE TABLE tag_labels (
    normalized   text PRIMARY KEY,
    tag_id       text NOT NULL REFERENCES tags ON DELETE CASCADE,
    label        text NOT NULL,
    is_canonical boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX tag_labels_one_canonical ON tag_labels (tag_id) WHERE is_canonical;
CREATE INDEX tag_labels_trgm_idx ON tag_labels USING gin (normalized gin_trgm_ops);

CREATE TABLE tag_names (
    tag_id text NOT NULL REFERENCES tags ON DELETE CASCADE,
    lang   text NOT NULL,
    name   text NOT NULL,
    PRIMARY KEY (tag_id, lang)
);

CREATE TABLE fact_tags (
    fact_id text NOT NULL REFERENCES facts ON DELETE CASCADE,
    tag_id  text NOT NULL REFERENCES tags,
    PRIMARY KEY (fact_id, tag_id)
);
CREATE INDEX fact_tags_tag_idx ON fact_tags (tag_id);

-- The research grid (H3). Cells appear here once anything touches them.
CREATE TABLE research_cells (
    cell               text PRIMARY KEY,
    resolution         integer NOT NULL,
    state              text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'claimed', 'drafted', 'reviewed', 'done')),
    claimed_by_run     text REFERENCES pipeline_runs,
    claimed_until      timestamptz,
    last_researched_at timestamptz,
    notes              text,
    geom               geometry(Polygon, 4326) NOT NULL
);
CREATE INDEX research_cells_geom_idx ON research_cells USING gist (geom);

CREATE TABLE reports (
    id          bigserial PRIMARY KEY,
    fact_id     text NOT NULL REFERENCES facts,
    reason      text NOT NULL CHECK (reason IN ('wrong', 'outdated', 'location', 'offensive', 'other')),
    message     text CHECK (char_length(message) <= 1000),
    app_version text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    state       text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved')),
    resolution  text,
    resolved_at timestamptz
);
CREATE INDEX reports_open_idx ON reports (created_at) WHERE state = 'open';

-- Anonymous "no stories here" signals, counted per coarse cell and day. No per-request rows are kept.
CREATE TABLE demand (
    cell  text NOT NULL,
    day   date NOT NULL,
    count integer NOT NULL DEFAULT 0,
    PRIMARY KEY (cell, day)
);

CREATE TABLE publications (
    id              bigserial PRIMARY KEY,
    channel         text NOT NULL CHECK (channel IN ('staging', 'production')),
    content_version text NOT NULL,
    manifest        jsonb NOT NULL,
    places          integer NOT NULL,
    facts           integer NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    run_id          text REFERENCES pipeline_runs,
    notes           text
);

-- The only two things the public API may do. Both run with the owner's rights, validate their input,
-- and are the only grants the api role has.
CREATE FUNCTION psst.submit_report(p_fact_id text, p_reason text, p_message text, p_app_version text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = psst, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM facts WHERE id = p_fact_id AND state = 'published') THEN
        RAISE EXCEPTION 'unknown fact' USING ERRCODE = 'P0002';
    END IF;
    INSERT INTO reports (fact_id, reason, message, app_version)
    VALUES (p_fact_id, p_reason, nullif(left(p_message, 1000), ''), left(p_app_version, 40));
    UPDATE facts SET needs_review = true WHERE id = p_fact_id AND NOT needs_review;
END;
$$;

CREATE FUNCTION psst.record_demand(p_cell text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = psst, pg_temp AS $$
BEGIN
    IF p_cell !~ '^[0-9a-f]{15}$' THEN
        RAISE EXCEPTION 'bad cell' USING ERRCODE = '22023';
    END IF;
    INSERT INTO demand (cell, day, count) VALUES (p_cell, current_date, 1)
    ON CONFLICT (cell, day) DO UPDATE SET count = demand.count + 1;
END;
$$;

REVOKE ALL ON FUNCTION psst.submit_report(text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION psst.record_demand(text) FROM PUBLIC;
GRANT USAGE ON SCHEMA psst TO psst_api;
GRANT EXECUTE ON FUNCTION psst.submit_report(text, text, text, text) TO psst_api;
GRANT EXECUTE ON FUNCTION psst.record_demand(text) TO psst_api;
