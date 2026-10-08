"""RentCastSource against a fake HTTP opener serving a sample of real responses."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from rentscout.filters import hard_filter
from rentscout.llm import RuleBasedLLM
from rentscout.pipeline import daily_run
from rentscout.profile import load_profile
from rentscout.sources.rentcast import QuotaExhausted, RentCastError, RentCastSource
from rentscout.state import Store
from rentscout.tools import build_registry

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads((ROOT / "tests/data/rentcast_sample.json").read_text())
MONTH = "2026-10"


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, total: int | None) -> None:
        super().__init__(body)
        self.headers = {"X-Total-Count": str(total)} if total is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b"{}"))


class Opener:
    """Plays back a script of responses or exceptions, recording each request."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(records=SAMPLE, total=None):
    return FakeResponse(json.dumps(records).encode(), len(records) if total is None else total)


def _source(store, opener, cap=40):
    return RentCastSource(
        api_key="test-key", store=store, month=MONTH, monthly_cap=cap,
        min_beds=1, max_price=2200, opener=opener, sleep=lambda s: None,
    )


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rs.db")
    yield s
    s.close()


def test_maps_structured_fields_and_zip_neighborhoods(store):
    listings = _source(store, Opener(ok())).fetch()
    by_addr = {l.address: l for l in listings}
    roy = by_addr["601 E Roy St, Seattle, WA 98102"]
    assert roy.id.startswith("rentcast:")
    assert roy.neighborhood == "Capitol Hill"
    assert roy.attrs["price_per_sqft"] == round(1775 / 675, 2)
    assert roy.description == ""
    assert roy.url.startswith("https://www.google.com/maps/search/")
    no_sqft = next(l for l in listings if l.sqft is None)
    assert "price_per_sqft" not in no_sqft.attrs


def test_request_carries_key_and_profile_filters(store):
    opener = Opener(ok())
    _source(store, opener).fetch()
    req = opener.requests[0]
    assert req.get_header("X-api-key") == "test-key"
    assert "bedrooms=1:" in req.full_url and "price=:2200" in req.full_url


def test_snapshot_only_when_the_page_holds_every_match(store):
    src = _source(store, Opener(ok(), ok(total=999)))
    src.fetch()
    assert src.snapshot is True
    src.fetch()
    assert src.snapshot is False  # truncated: missing listings are not delisted


def test_quota_refuses_before_any_request(store):
    for _ in range(3):
        store.add_api_call(MONTH, "rentcast")
    opener = Opener(ok())
    with pytest.raises(QuotaExhausted):
        _source(store, opener, cap=3).fetch()
    assert opener.requests == []


def test_retries_transient_errors_and_charges_every_attempt(store):
    opener = Opener(_http_error(503), urllib.error.URLError("reset"), ok())
    assert len(_source(store, opener).fetch()) == len(SAMPLE)
    assert store.api_calls(MONTH, "rentcast") == 3


def test_auth_errors_are_not_retried(store):
    opener = Opener(_http_error(403), ok())
    with pytest.raises(RentCastError, match="HTTP 403"):
        _source(store, opener).fetch()
    assert store.api_calls(MONTH, "rentcast") == 1


def test_retry_stops_at_the_quota(store):
    opener = Opener(_http_error(500), _http_error(500), ok())
    with pytest.raises(QuotaExhausted):
        _source(store, opener, cap=2).fetch()
    assert store.api_calls(MONTH, "rentcast") == 2


def test_unknown_zip_is_filtered_when_neighborhoods_are_set(store):
    record = dict(SAMPLE[1], zipCode="99999", id="far-away")  # within budget
    profile, _ = load_profile(ROOT / "examples/profile_live.toml")
    passed, filtered = hard_filter(profile, _source(store, Opener(ok([record]))).fetch())
    assert passed == [] and "unknown" in filtered[0][1]


def test_live_listings_run_through_the_pipeline_and_persist(store, tmp_path):
    profile, caps = load_profile(ROOT / "examples/profile_live.toml")
    result = daily_run(
        source=_source(store, Opener(ok())), store=store, profile=profile, caps=caps,
        llm=RuleBasedLLM(), registry=build_registry(store, {}),
        run_date="2026-10-08", out_dir=tmp_path,
    )
    assert result.new_count == len(SAMPLE)
    stored = store.listing(f"rentcast:{SAMPLE[1]['id']}")
    assert json.loads(stored["attributes"])["neighborhoods"][0] == "Capitol Hill"


def test_failed_fetch_leaves_a_failed_run(store, tmp_path):
    profile, caps = load_profile(ROOT / "examples/profile_live.toml")
    with pytest.raises(RentCastError):
        daily_run(
            source=_source(store, Opener(_http_error(401))), store=store,
            profile=profile, caps=caps, llm=RuleBasedLLM(),
            registry=build_registry(store, {}), run_date="2026-10-08", out_dir=tmp_path,
        )
    assert store.all_runs()[0]["status"].startswith("failed: ")
