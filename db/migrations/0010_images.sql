SET search_path = psst, public;

-- Photos of places. Each goes through the same lifecycle as a fact: a draft from research (or an upload
-- from the owner), a review that checks the photo shows the right thing and isn't misleading, and
-- publishing through staging. Every image carries full attribution, and we host our own resized copies.
CREATE TABLE images (
    id              text PRIMARY KEY CHECK (id ~ '^im_[0-9a-hjkmnp-tv-z]{10}$'),
    place_id        text NOT NULL REFERENCES places,
    position        integer NOT NULL DEFAULT 0,
    -- A current photo, or a historic one (for a "then and now" view).
    kind            text NOT NULL DEFAULT 'photo' CHECK (kind IN ('photo', 'historic')),
    year            integer CHECK (year BETWEEN 1820 AND 2100),
    -- Where it came from: Wikimedia Commons, Geograph, Flickr, an archive, or the owner's own camera.
    source          text NOT NULL CHECK (source IN ('commons', 'geograph', 'flickr', 'archive', 'owner')),
    source_ref      text NOT NULL,
    source_url      text NOT NULL CHECK (source_url ~ '^https://'),
    title           text,
    author          text NOT NULL CHECK (author <> ''),
    author_url      text,
    license         text NOT NULL,
    license_url     text,
    alt_text        text NOT NULL CHECK (char_length(alt_text) BETWEEN 10 AND 300),
    focus_x         real NOT NULL DEFAULT 0.5 CHECK (focus_x BETWEEN 0 AND 1),
    focus_y         real NOT NULL DEFAULT 0.5 CHECK (focus_y BETWEEN 0 AND 1),
    -- Our copies, named by their hashes, and their sizes.
    full_file       text NOT NULL,
    full_width      integer NOT NULL,
    full_height     integer NOT NULL,
    thumb_file      text NOT NULL,
    thumb_width     integer NOT NULL,
    thumb_height    integer NOT NULL,
    state           text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'reviewed', 'published', 'retired')),
    added_by        text NOT NULL,
    added_run       text NOT NULL REFERENCES pipeline_runs,
    reviewed_at     timestamptz,
    reviewed_by     text,
    review_run      text REFERENCES pipeline_runs,
    review_notes    text,
    published_at    timestamptz,
    retired_at      timestamptz,
    retire_reason   text,
    needs_review    boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (place_id, source, source_ref),
    CHECK (state NOT IN ('reviewed', 'published') OR reviewed_at IS NOT NULL),
    CHECK (state <> 'published' OR published_at IS NOT NULL),
    CHECK (state <> 'retired' OR (retired_at IS NOT NULL AND retire_reason IS NOT NULL)),
    CHECK (kind <> 'historic' OR year IS NOT NULL)
);
CREATE INDEX images_place_idx ON images (place_id, position);
CREATE INDEX images_state_idx ON images (state);
CREATE TRIGGER images_touch BEFORE UPDATE ON images FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Every lifecycle change and edit of an image, kept forever, like fact_events.
CREATE TABLE image_events (
    id         bigserial PRIMARY KEY,
    image_id   text NOT NULL REFERENCES images ON DELETE CASCADE,
    at         timestamptz NOT NULL DEFAULT now(),
    from_state text,
    to_state   text NOT NULL,
    changes    text[] NOT NULL DEFAULT '{}',
    actor      text NOT NULL,
    run_id     text,
    note       text
);
CREATE INDEX image_events_image_idx ON image_events (image_id, at);

CREATE FUNCTION psst.record_image_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    changed text[] := '{}';
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.alt_text IS DISTINCT FROM OLD.alt_text THEN changed := array_append(changed, 'alt_text'); END IF;
        IF NEW.focus_x IS DISTINCT FROM OLD.focus_x OR NEW.focus_y IS DISTINCT FROM OLD.focus_y THEN
            changed := array_append(changed, 'focus');
        END IF;
        IF NEW.kind IS DISTINCT FROM OLD.kind OR NEW.year IS DISTINCT FROM OLD.year THEN
            changed := array_append(changed, 'kind');
        END IF;
        IF NEW.position IS DISTINCT FROM OLD.position THEN changed := array_append(changed, 'position'); END IF;
        IF NEW.needs_review IS DISTINCT FROM OLD.needs_review THEN
            changed := array_append(changed, CASE WHEN NEW.needs_review THEN 'flagged' ELSE 'unflagged' END);
        END IF;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.state IS DISTINCT FROM OLD.state OR cardinality(changed) > 0 THEN
        INSERT INTO psst.image_events (image_id, from_state, to_state, changes, actor, run_id, note)
        VALUES (NEW.id, CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.state END, NEW.state, changed,
                coalesce(nullif(current_setting('psst.actor', true), ''), current_user),
                nullif(current_setting('psst.run', true), ''),
                nullif(current_setting('psst.note', true), ''));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER images_events AFTER INSERT OR UPDATE ON images FOR EACH ROW EXECUTE FUNCTION psst.record_image_event();

-- Which images a review run actually looked at. Approving an image nobody opened is refused, as with sources.
CREATE TABLE image_views (
    run_id    text NOT NULL REFERENCES pipeline_runs,
    image_id  text NOT NULL REFERENCES images ON DELETE CASCADE,
    viewed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, image_id)
);
