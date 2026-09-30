-- The runs the import created to record who wrote the old area files were never marked finished.
-- They describe finished work, so they end when they began.
UPDATE pipeline_runs SET finished_at = started_at
WHERE finished_at IS NULL AND (kind = 'import' OR notes LIKE 'Research for the legacy area %');
