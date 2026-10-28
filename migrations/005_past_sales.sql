-- 005_past_sales: where a community's centre came from, and how many parcels were linked
-- (past_sales.n counts only the linked parcels with a usable sale estimate).

ALTER TABLE communities ADD COLUMN geo_method TEXT;
ALTER TABLE past_sales ADD COLUMN linked INTEGER;
