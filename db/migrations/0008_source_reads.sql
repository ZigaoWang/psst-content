-- Which sources a run actually opened with psst fetch. A review can only approve or edit a fact once its
-- run has opened every source the fact cites, so approving something unread is impossible.
CREATE TABLE source_reads (
    run_id  text NOT NULL REFERENCES pipeline_runs,
    url_key text NOT NULL,
    read_at timestamptz NOT NULL DEFAULT now(),
    ok      boolean NOT NULL,
    PRIMARY KEY (run_id, url_key)
);
