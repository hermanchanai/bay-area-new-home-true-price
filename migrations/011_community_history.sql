-- What each community advertises, one row per community page per fetch date: the lowest price on the page and the
-- page's offers, whether or not any home can be priced yet. price_history covers homes only.
CREATE TABLE community_history (
    snapshot_date TEXT NOT NULL,
    community_url TEXT NOT NULL,
    from_price    INTEGER,
    incentives    TEXT NOT NULL DEFAULT '[]',   -- JSON: [{label, kind, amount_usd, amount_is_up_to, rate, apr, expires}]
    carried       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_date, community_url)
);
