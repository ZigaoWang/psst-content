SET search_path = psst, public;

-- Every "what did this run do" lookup (the admin page, review checks, reopen) filters by run. Without these,
-- each one scans the whole table, which stops being cheap long before content reaches 100x its size.
CREATE INDEX IF NOT EXISTS facts_research_run_idx ON facts (research_run);
CREATE INDEX IF NOT EXISTS facts_review_run_idx ON facts (review_run);
CREATE INDEX IF NOT EXISTS fact_events_run_idx ON fact_events (run_id);
CREATE INDEX IF NOT EXISTS guides_research_run_idx ON guides (research_run);
CREATE INDEX IF NOT EXISTS guides_review_run_idx ON guides (review_run);
CREATE INDEX IF NOT EXISTS images_added_run_idx ON images (added_run);
CREATE INDEX IF NOT EXISTS images_review_run_idx ON images (review_run);
CREATE INDEX IF NOT EXISTS reports_fact_idx ON reports (fact_id);
CREATE INDEX IF NOT EXISTS source_reads_run_idx ON source_reads (run_id, read_at);
