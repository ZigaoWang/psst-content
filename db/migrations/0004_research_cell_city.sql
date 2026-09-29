-- Research cells belong to the city they were planned for, so work can be claimed city by city.
ALTER TABLE research_cells ADD COLUMN city_id bigint REFERENCES admin_areas;
CREATE INDEX research_cells_city_idx ON research_cells (city_id, state);
