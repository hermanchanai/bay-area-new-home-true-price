-- 009_assessor_records: no estimates (Herman, 2026-11-03). Each linked home's assessor record as the roll
-- prints it, plus Alameda's recorded sale price; a community shows the median assessed value.

DROP TABLE past_sales;
CREATE TABLE past_sales (
    community_id INTEGER PRIMARY KEY REFERENCES communities(id),
    n            INTEGER NOT NULL,     -- homes with a full assessed value
    median_value INTEGER NOT NULL,
    p25          INTEGER NOT NULL,
    p75          INTEGER NOT NULL,
    period       TEXT NOT NULL,        -- sale dates (deed date, or year built in San Joaquin)
    method       TEXT NOT NULL CHECK (method = 'assessed_value'),
    linked       INTEGER
);

CREATE TABLE IF NOT EXISTS past_records (
    community_id   INTEGER NOT NULL REFERENCES communities(id),
    county         TEXT NOT NULL,
    apn            TEXT NOT NULL,
    address        TEXT,
    assessed_value INTEGER,            -- NULL when the value is not yet reset to the sale
    roll_year      INTEGER NOT NULL,
    sale_date      TEXT,
    living_sqft    INTEGER,
    beds           INTEGER,
    recorded_price INTEGER,            -- Alameda's transfer-tax price, where recorded
    PRIMARY KEY (community_id, county, apn)
);
