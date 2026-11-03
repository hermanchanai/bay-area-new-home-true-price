-- Every community with county records gets a row: under five valued homes have no median, and values withheld as
-- likely part-built are counted. Derived data: rerun tools.link_parcels' rebuild_records after this.
DROP TABLE IF EXISTS past_sales;
CREATE TABLE past_sales (
    community_id INTEGER PRIMARY KEY REFERENCES communities(id),
    n            INTEGER NOT NULL,
    median_value INTEGER,
    p25          INTEGER,
    p75          INTEGER,
    period       TEXT,
    method       TEXT NOT NULL CHECK (method = 'assessed_value'),
    linked       INTEGER,
    withheld     INTEGER NOT NULL DEFAULT 0
);
