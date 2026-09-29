-- Wikidata and OpenStreetMap links on boundaries, used for better English names and for OSM boundaries.
SET search_path = psst, public;
ALTER TABLE admin_areas ADD COLUMN wikidata_id text;
ALTER TABLE admin_areas ADD COLUMN osm_admin_level integer;
CREATE INDEX admin_areas_wikidata_idx ON admin_areas (wikidata_id) WHERE wikidata_id IS NOT NULL;
