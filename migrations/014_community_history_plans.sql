-- Each floor plan's advertised price on that date: JSON {name: price}.
ALTER TABLE community_history ADD COLUMN plans TEXT;
