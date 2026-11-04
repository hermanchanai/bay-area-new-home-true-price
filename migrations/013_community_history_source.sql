-- Where a row came from when it isn't our own daily check: a link to the Internet Archive's copy of the page.
-- incentives NULL = the copy showed no priced offers, which says nothing either way. SQLite can't drop NOT NULL in place.
CREATE TABLE community_history_new (
    snapshot_date TEXT NOT NULL,
    community_url TEXT NOT NULL,
    from_price    INTEGER,
    incentives    TEXT,
    carried       INTEGER NOT NULL DEFAULT 0,
    source_url    TEXT,
    PRIMARY KEY (snapshot_date, community_url)
);
INSERT INTO community_history_new (snapshot_date, community_url, from_price, incentives, carried)
    SELECT snapshot_date, community_url, from_price, incentives, carried FROM community_history;
DROP TABLE community_history;
ALTER TABLE community_history_new RENAME TO community_history;
