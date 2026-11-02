-- A home whose page wasn't fetched (or extracted) that day keeps its last price so it doesn't vanish
-- (tools/daily_refresh.py carry steps); its row is marked carried = 1: still listed, but not a fresh check.
ALTER TABLE price_history ADD COLUMN carried INTEGER NOT NULL DEFAULT 0;
