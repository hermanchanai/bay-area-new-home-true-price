-- 001_init: core schema. Plan §5.

CREATE TABLE IF NOT EXISTS builders (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    site             TEXT NOT NULL,
    discovery_method TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS communities (
    id             INTEGER PRIMARY KEY,
    builder_id     INTEGER NOT NULL REFERENCES builders(id),
    name           TEXT NOT NULL,
    city           TEXT,
    county_fips    TEXT,
    lat            REAL,
    lng            REAL,
    url            TEXT NOT NULL UNIQUE,
    product        TEXT CHECK (product IN ('SFD', 'TH', 'condo', 'mixed')),
    status         TEXT,
    age_restricted INTEGER NOT NULL DEFAULT 0,
    fetched_at     TEXT,
    page_hash      TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id           INTEGER PRIMARY KEY,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    name         TEXT NOT NULL,
    beds_min     REAL,
    beds_max     REAL,
    baths        REAL,
    half_baths   INTEGER,
    sqft_min     INTEGER,
    sqft_max     INTEGER,
    stories      INTEGER,
    garage       INTEGER,
    from_price   INTEGER,
    image_url    TEXT,
    layout_json  TEXT,
    UNIQUE (community_id, name)
);

CREATE TABLE IF NOT EXISTS homes (
    id             INTEGER PRIMARY KEY,
    community_id   INTEGER NOT NULL REFERENCES communities(id),
    plan_id        INTEGER REFERENCES plans(id),
    address        TEXT,
    lot            TEXT,
    price          INTEGER,
    price_basis    TEXT NOT NULL CHECK (price_basis IN ('quick_move_in', 'plan_starting_price', 'community_min')),
    move_in_status TEXT CHECK (move_in_status IN ('now', 'month', 'under_construction')),
    ready_date     TEXT,
    beds           REAL,
    baths          REAL,
    sqft           INTEGER,
    url            TEXT,
    fetched_at     TEXT
);

CREATE TABLE IF NOT EXISTS incentives (
    id               INTEGER PRIMARY KEY,
    scope            TEXT NOT NULL CHECK (scope IN ('builder', 'community', 'home')),
    scope_id         INTEGER NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('flat_cash', 'price_cut', 'closing_credit', 'perm_rate',
                                                   'temp_buydown', 'arm_promo', 'first_year_rate', 'unquantified')),
    amount_usd       INTEGER,
    amount_is_up_to  INTEGER NOT NULL DEFAULT 0,
    rate             REAL,
    apr              REAL,
    structure        TEXT,
    lender_condition INTEGER NOT NULL DEFAULT 0,
    pool_id          TEXT,
    expires          TEXT,
    fine_print       TEXT NOT NULL,
    source_url       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_rates (
    series     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    rate       REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (series, as_of)
);

CREATE TABLE IF NOT EXISTS incentive_values (
    incentive_id     INTEGER NOT NULL REFERENCES incentives(id),
    home_id          INTEGER NOT NULL REFERENCES homes(id),
    market_rate      REAL NOT NULL,
    rate_date        TEXT NOT NULL,
    value_low        INTEGER NOT NULL,
    value_high       INTEGER NOT NULL,
    method           TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    PRIMARY KEY (incentive_id, home_id)
);

CREATE TABLE IF NOT EXISTS true_prices (
    home_id     INTEGER PRIMARY KEY REFERENCES homes(id),
    asking      INTEGER NOT NULL,
    value_low   INTEGER NOT NULL,
    value_high  INTEGER NOT NULL,
    true_low    INTEGER NOT NULL,
    true_high   INTEGER NOT NULL,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parcels (
    county             TEXT NOT NULL,
    apn                TEXT NOT NULL,
    address            TEXT,
    land               INTEGER,
    imps               INTEGER,
    total              INTEGER,
    roll_year          INTEGER,
    deed_date          TEXT,
    deed_doc           TEXT,
    year_built         INTEGER,
    use_code           TEXT,
    lat                REAL,
    lng                REAL,
    transfer_tax_price INTEGER,
    PRIMARY KEY (county, apn)
);

CREATE TABLE IF NOT EXISTS community_parcels (
    community_id INTEGER NOT NULL REFERENCES communities(id),
    county       TEXT NOT NULL,
    apn          TEXT NOT NULL,
    match_method TEXT NOT NULL,
    confidence   REAL NOT NULL,
    PRIMARY KEY (community_id, county, apn)
);

CREATE TABLE IF NOT EXISTS past_sales (
    community_id     INTEGER PRIMARY KEY REFERENCES communities(id),
    n                INTEGER NOT NULL,
    median_est_price INTEGER NOT NULL,
    p25              INTEGER NOT NULL,
    p75              INTEGER NOT NULL,
    period           TEXT NOT NULL,
    method           TEXT NOT NULL CHECK (method IN ('transfer_tax', 'value_backed_out', 'mixed'))
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id          INTEGER PRIMARY KEY,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    purpose     TEXT NOT NULL,
    cache_key   TEXT NOT NULL,
    in_tokens   INTEGER,
    out_tokens  INTEGER,
    cost_usd    REAL,
    latency_ms  INTEGER,
    ok          INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_results (
    page_id  TEXT NOT NULL,
    field    TEXT NOT NULL,
    expected TEXT,
    got      TEXT,
    match    INTEGER NOT NULL,
    run_at   TEXT NOT NULL
);
