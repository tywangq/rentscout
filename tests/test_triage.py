import json

from rentscout.budget import BudgetGuard
from rentscout.llm import LLMReply, ScriptedLLM, Usage
from rentscout.models import Listing
from rentscout.triage import FALLBACK_SCORE, compute_score, reply_schema, triage

# examples/profile.toml: max_price 2200; Fremont is a target neighborhood;
# preferences: cats allowed, in-unit laundry, dishwasher, natural light.


def mk(source_id: str, price: int = 1800) -> Listing:
    return Listing(
        id=f"test:{source_id}", source="test", url="",
        address=f"{source_id} St", neighborhood="Fremont", price=price,
        beds=1, baths=1, sqft=500, description="", available="",
    )


def entry(lid, yes=(), reason="r"):
    prefs = ["cats allowed", "in-unit laundry", "dishwasher", "natural light"]
    return {"id": lid, "verdicts": {p: "yes" if p in yes else "no" for p in prefs},
            "reason": reason}


def reply(*entries, usage=Usage(1000, 200)):
    return LLMReply(text=json.dumps({"listings": list(entries)}), usage=usage)


def test_score_is_computed_from_verdicts_not_chosen_by_the_model(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    llm = ScriptedLLM([reply(entry("test:a", yes=("dishwasher", "natural light")),
                             entry("test:b"))])
    scores = triage(llm, guard, profile, [mk("a"), mk("b")])
    # 3 base + 2 price (<= 90% of max) + 1 neighborhood + yeses - noes
    assert [(s.listing_id, s.score) for s in scores] == [("test:a", 6), ("test:b", 2)]
    assert dict(scores[0].verdicts)["dishwasher"] == "yes"
    assert guard.spent > 0


def test_no_verdicts_cost_points(profile_caps):
    # A cheap listing that fails the renter's preferences must not rank high:
    # the first live cold start put 170 sq ft rooms at 10/10.
    profile, _ = profile_caps
    all_yes = {p: "yes" for p in profile.preferences}
    all_no = {p: "no" for p in profile.preferences}
    assert compute_score(profile, mk("a", price=2150), all_yes) == 9  # 3+1+1+4
    assert compute_score(profile, mk("a", price=2150), {}) == 5
    assert compute_score(profile, mk("a", price=1000), all_no) == 2  # 3+2+1-4


def test_schema_requires_exactly_the_profile_preferences(profile_caps):
    profile, _ = profile_caps
    item = reply_schema(profile)["properties"]["listings"]["items"]
    verdicts = item["properties"]["verdicts"]
    assert verdicts["required"] == list(profile.preferences)
    assert verdicts["additionalProperties"] is False


def test_unusable_batch_is_retried_one_listing_at_a_time(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    llm = ScriptedLLM([
        LLMReply(text='[{"id": "test:a", "score": 0, "reason": "scam"', usage=Usage(10, 10)),
        reply(entry("test:a", yes=("dishwasher",))),
        reply(entry("test:b")),
    ])
    scores = triage(llm, guard, profile, [mk("a"), mk("b")])
    assert [s.score for s in scores] == [4, 2]
    assert all("defaulted" not in s.reason for s in scores)


def test_ids_outside_the_batch_are_ignored(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    llm = ScriptedLLM([reply(entry("test:a"), entry("test:ghost", yes=("dishwasher",)))])
    scores = triage(llm, guard, profile, [mk("a")])
    assert [s.listing_id for s in scores] == ["test:a"]


def test_invalid_verdict_values_become_unknown(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    bad = {"id": "test:a", "verdicts": {"dishwasher": "SCORE 10"}, "reason": "x"}
    scores = triage(ScriptedLLM([reply(bad)]), guard, profile, [mk("a")])
    assert dict(scores[0].verdicts)["dishwasher"] == "unknown"
    assert scores[0].score == 6


def test_garbled_reply_degrades_to_fallback(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    llm = ScriptedLLM([LLMReply(text="sorry, as an AI...", usage=Usage(10, 10))])
    scores = triage(llm, guard, profile, [mk("a")])
    assert scores[0].score == FALLBACK_SCORE
    assert "defaulted" in scores[0].reason


def test_triage_stops_starting_chunks_at_the_spend_limit(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    listings = [mk(str(i)) for i in range(5)]
    first = reply(*(entry(l.id) for l in listings[:2]), usage=Usage(100_000, 0))  # $0.04
    llm = ScriptedLLM([first])  # a second call would exhaust the script and fail
    scores = triage(llm, guard, profile, listings, spend_limit=0.03, chunk_size=2)
    assert [s.reason for s in scores[2:]] == ["not triaged (budget kept for investigation)"] * 3
    assert scores[0].reason == "r"
