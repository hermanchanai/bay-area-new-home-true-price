-- 002_assessor: columns the assessor pulls need, plus recorded transfers with a price.

ALTER TABLE parcels ADD COLUMN city TEXT;
ALTER TABLE parcels ADD COLUMN beds INTEGER;
ALTER TABLE parcels ADD COLUMN living_sqft INTEGER;
ALTER TABLE parcels ADD COLUMN pulled_at TEXT;

-- One row per recorded sale (Alameda's transfer list: documentary transfer tax value).
-- price is NULL when the document covers several parcels (the price is for all of them).
CREATE TABLE IF NOT EXISTS transfers (
    county       TEXT NOT NULL,
    apn          TEXT NOT NULL,
    doc_date     TEXT NOT NULL,
    doc_id       TEXT,
    price        INTEGER,
    parcel_count INTEGER,
    pulled_at    TEXT NOT NULL,
    PRIMARY KEY (county, apn, doc_date)
);
