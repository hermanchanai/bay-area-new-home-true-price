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
