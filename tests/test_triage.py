import json

from rentscout.budget import BudgetGuard
from rentscout.llm import LLMReply, ScriptedLLM, Usage
from rentscout.models import Listing
from rentscout.triage import FALLBACK_SCORE, triage


def mk(source_id: str) -> Listing:
    return Listing(
        id=f"test:{source_id}", source="test", url="",
        address=f"{source_id} St", neighborhood="Fremont", price=1800,
        beds=1, baths=1, sqft=500, description="", available="",
    )


def test_valid_reply_parsed_and_charged(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    reply = json.dumps(
        [
            {"id": "test:a", "score": 8, "reason": "good"},
            {"id": "test:b", "score": 99, "reason": "overflow"},
        ]
    )
    llm = ScriptedLLM([LLMReply(text=reply, usage=Usage(1000, 200))])
    scores = triage(llm, guard, profile, [mk("a"), mk("b")])
    assert [(s.listing_id, s.score) for s in scores] == [
        ("test:a", 8),
        ("test:b", 10),  # clamped
    ]
    assert guard.spent > 0


def test_garbled_reply_degrades_to_fallback(profile_caps):
    profile, caps = profile_caps
    guard = BudgetGuard(caps, month_spent=0.0)
    llm = ScriptedLLM([LLMReply(text="sorry, as an AI...", usage=Usage(10, 10))])
    scores = triage(llm, guard, profile, [mk("a")])
    assert scores[0].score == FALLBACK_SCORE
    assert "defaulted" in scores[0].reason
