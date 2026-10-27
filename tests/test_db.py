import sqlite3

import pytest

from tools import db


def test_migrate_creates_every_table_once():
    conn = db.connect(":memory:")
    names = set(db.tables(conn))
    for t in ("builders", "communities", "plans", "homes", "incentives", "incentive_values",
              "true_prices", "market_rates", "parcels", "community_parcels", "past_sales",
              "llm_calls", "eval_results"):
        assert t in names
    assert db.migrate(conn) == []  # second run applies nothing


def test_incentive_kind_is_constrained():
    conn = db.connect(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO incentives (scope, scope_id, kind, fine_print, source_url) "
                     "VALUES ('community', 1, 'free_tv', 'x', 'https://example.com')")


def test_home_needs_existing_community():
    conn = db.connect(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO homes (community_id, price_basis) VALUES (999, 'quick_move_in')")
