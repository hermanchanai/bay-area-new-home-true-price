"""What the community page says about its starting price."""
from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()


def test_starting_price_is_not_called_the_lowest_price():
    """A listed home can ask less than the floor plan price, so the page can't call that price the lowest."""
    assert "the lowest price on the builder's page" not in APP
    assert "the builder's advertised starting price" in APP


def test_page_says_so_when_a_listed_home_asks_less():
    assert "c.min_asking < c.advertised_from" in APP
    assert "Some homes below are listed for less." in APP


def test_a_rate_offer_shows_its_rate_on_the_community_page():
    # Trailview's label says only "CONV 7/6 ARM": the 4.25% behind its dollar value wasn't on the page (2026-11-04).
    assert "${i.rate}% rate${i.apr ? `, ${i.apr}% APR`" in APP


def test_records_with_no_sale_dates_show_years_built_not_a_sold_column():
    assert 'ps.built_period ? "Built" : "Sold"' in APP
    assert '"built " + esc(ps.built_period)' in APP
    assert "r.year_built || r.sale_date" in APP
