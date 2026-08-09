from conftest import SCENARIO

from rentscout.sources.fixture import FixtureSource


def test_available_days():
    assert FixtureSource.available_days(SCENARIO) == [1, 2, 3]


def test_fetch_day_one():
    source = FixtureSource(SCENARIO, 1)
    listings = source.fetch()
    assert len(listings) == 7
    assert all(l.id.startswith("fixture:") for l in listings)
    assert source.snapshot is True
    assert source.expected["gold_ids"][0] == "fixture:fx-101"


def test_commute_table():
    source = FixtureSource(SCENARIO, 1)
    commutes = source.commutes()
    assert commutes["416 E Olive Way, Seattle, WA"] == 15
