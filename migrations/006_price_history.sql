-- Daily price history: one row per home per day its builder's pages were read (tools/snapshot_prices.py).
-- homes is rebuilt on every load, so rows are keyed by home_key (builder + community page + address or lot),
-- which stays the same across loads, not by homes.id.
CREATE TABLE IF NOT EXISTS price_history (
    snapshot_date  TEXT NOT NULL,     -- date the builder pages were fetched
    home_key       TEXT NOT NULL,
    builder        TEXT NOT NULL,
    community      TEXT,
    city           TEXT,
    county_fips    TEXT,
    address        TEXT,
    lot            TEXT,
    beds           REAL,
    baths          REAL,
    sqft           INTEGER,
    move_in_status TEXT,
    asking         INTEGER,
    was_price      INTEGER,
    true_low       INTEGER,
    true_high      INTEGER,
    market_rate    REAL,
    offers         TEXT,              -- JSON: [{label, kind, value_low, value_high, counted}]
    source_url     TEXT,
    PRIMARY KEY (snapshot_date, home_key)
);
CREATE INDEX IF NOT EXISTS price_history_home ON price_history (home_key, snapshot_date);
