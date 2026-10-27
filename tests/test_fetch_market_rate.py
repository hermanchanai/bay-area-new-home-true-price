import pytest

from tools.fetch_market_rate import parse_latest

GOOD = "observation_date,MORTGAGE30US\n2026-10-08,7.40\n2026-10-15,7.40\n2026-10-22,7.32\n"


def test_takes_newest_row():
    assert parse_latest(GOOD) == ("2026-10-22", 7.32)


def test_skips_missing_week():
    assert parse_latest(GOOD + "2026-10-29,.\n") == ("2026-10-22", 7.32)


def test_rejects_wrong_series():
    with pytest.raises(ValueError):
        parse_latest("observation_date,DGS10\n2026-10-22,4.10\n")


def test_rejects_empty():
    with pytest.raises(ValueError):
        parse_latest("observation_date,MORTGAGE30US\n2026-10-22,.\n")
