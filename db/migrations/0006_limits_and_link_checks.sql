-- At most 20 reports per story per day are kept. One is enough to flag a story for review; beyond that,
-- a flood only buries the real ones.
CREATE OR REPLACE FUNCTION psst.submit_report(p_fact_id text, p_reason text, p_message text, p_app_version text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = psst, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM facts WHERE id = p_fact_id AND state = 'published') THEN
        RAISE EXCEPTION 'unknown fact' USING ERRCODE = 'P0002';
    END IF;
    IF (SELECT count(*) FROM reports WHERE fact_id = p_fact_id AND created_at > now() - interval '1 day') >= 20 THEN
        RETURN;
    END IF;
    INSERT INTO reports (fact_id, reason, message, app_version)
    VALUES (p_fact_id, p_reason, nullif(left(p_message, 1000), ''), left(p_app_version, 40));
    UPDATE facts SET needs_review = true WHERE id = p_fact_id AND NOT needs_review;
END;
$$;

-- The weekly link check: how many checks in a row a source has failed. A source is only called dead
-- after failing twice, a week apart, so a site that's down for an afternoon doesn't flag anything.
ALTER TABLE sources ADD COLUMN failed_checks integer NOT NULL DEFAULT 0;
