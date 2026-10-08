-- Cloud sessions log in as psst_agent (server/agent-access.sh), which can delete only from link tables. Guides
-- replace their source links, drop key facts in review, and release claims, so it needs the same there. Their
-- history stays append-only, like fact_events.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'psst_agent') THEN
        GRANT DELETE ON psst.guide_sources, psst.guide_key_facts, psst.guide_claims TO psst_agent;
        REVOKE UPDATE ON psst.guide_events FROM psst_agent;
    END IF;
END;
$$;
