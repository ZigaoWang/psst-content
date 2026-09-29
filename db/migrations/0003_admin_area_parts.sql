-- Boundaries split into small pieces. Testing a point against a few hundred vertices instead of a whole
-- country's outline makes hierarchy assignment fast.
SET search_path = psst, public;
CREATE TABLE admin_area_parts (
    area_id bigint NOT NULL REFERENCES admin_areas ON DELETE CASCADE,
    geom    geometry(Polygon, 4326) NOT NULL
);
CREATE INDEX admin_area_parts_geom_idx ON admin_area_parts USING gist (geom);
CREATE INDEX admin_area_parts_area_idx ON admin_area_parts (area_id);
