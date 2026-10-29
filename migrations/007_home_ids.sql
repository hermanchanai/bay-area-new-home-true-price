-- A home's id is permanent: tools/load_records.py rebuilds homes on every load, so the id comes from this map
-- (builder + community page + address or lot, as in price_history.home_key). Saved answers cite [H<id>] and the
-- site links #h/<id>; an id is never reused for another home.
CREATE TABLE IF NOT EXISTS home_ids (
    home_key TEXT PRIMARY KEY,
    id       INTEGER NOT NULL UNIQUE
);
