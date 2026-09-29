-- Record edits and review flags in a fact's history too, not only lifecycle changes, and say what changed.
ALTER TABLE fact_events ADD COLUMN changes text[] NOT NULL DEFAULT '{}';

CREATE OR REPLACE FUNCTION psst.record_fact_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    changed text[] := '{}';
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.category IS DISTINCT FROM OLD.category THEN changed := array_append(changed, 'category'); END IF;
        IF NEW.veracity IS DISTINCT FROM OLD.veracity THEN changed := array_append(changed, 'veracity'); END IF;
        IF NEW.headline IS DISTINCT FROM OLD.headline THEN changed := array_append(changed, 'headline'); END IF;
        IF NEW.short IS DISTINCT FROM OLD.short THEN changed := array_append(changed, 'short'); END IF;
        IF NEW.long IS DISTINCT FROM OLD.long THEN changed := array_append(changed, 'long'); END IF;
        IF NEW.place_id IS DISTINCT FROM OLD.place_id THEN changed := array_append(changed, 'place'); END IF;
        IF NEW.needs_review IS DISTINCT FROM OLD.needs_review THEN
            changed := array_append(changed, CASE WHEN NEW.needs_review THEN 'flagged' ELSE 'unflagged' END);
        END IF;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.state IS DISTINCT FROM OLD.state OR cardinality(changed) > 0 THEN
        INSERT INTO psst.fact_events (fact_id, from_state, to_state, actor, run_id, note, changes)
        VALUES (NEW.id,
                CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.state END,
                NEW.state,
                coalesce(nullif(current_setting('psst.actor', true), ''), current_user),
                nullif(current_setting('psst.run', true), ''),
                nullif(current_setting('psst.note', true), ''),
                changed);
    END IF;
    RETURN NEW;
END;
$$;
