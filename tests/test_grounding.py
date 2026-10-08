"""The groundedness checker, on notes modelled after real live-run output."""

from __future__ import annotations

from rentscout.evals.grounding import check_note, profile_numbers

LISTING = {"price": 2100, "sqft": 745, "details": {"price_per_sqft": 2.82}}
COMMUTE_15 = [{"name": "commute_time", "result": "15 min by bike"}]
PREFS = profile_numbers(("at least 600 sq ft", "under a week on the market"))


def check(note, listing=LISTING, log=COMMUTE_15):
    return check_note(note, listing, log, max_price=2200, max_commute=35,
                      profile_numbers=PREFS)


def test_grounded_note_from_live_run_passes():
    note = ("This Capitol Hill condo has 745 sqft at $2100 ($2.82 per sqft). "
            "The commute is about 15 minutes by bike, within the 35-minute max.")
    assert check(note) == ([], [])


def test_commute_with_no_tool_evidence_is_invented():
    hard, _ = check("The commute is about 20 minutes.", log=[])
    assert hard == ["invented_commute: '20 minutes'"]


def test_unknown_tool_result_is_not_evidence():
    log = [{"name": "commute_time", "result": "unknown (routing HTTP 403)"}]
    hard, _ = check("Roughly 25 min to work.", log=log)
    assert hard and hard[0].startswith("invented_commute")


def test_missing_sqft_cannot_be_filled_in():
    listing = dict(LISTING, sqft=None)
    hard, _ = check("At 650 sq ft it is roomy.", listing=listing)
    assert hard == ["invented_sqft: '650 sq ft'"]


def test_quoting_the_renters_threshold_is_fine():
    listing = dict(LISTING, sqft=None)
    assert check("Size is unknown, so the 600 sq ft preference is unverified.",
                 listing=listing) == ([], [])


def test_unsourced_dollar_figure_is_invented():
    hard, _ = check("Comparable units rent for $2400.")
    assert hard == ["invented_price: '$2400'"]


def test_hedged_area_knowledge_is_flagged_soft():
    # Verbatim pattern from the first live run, when routing was down.
    hard, soft = check("Capitol Hill is generally within the 35-minute range.", log=[])
    assert hard == [] and soft == ["speculation: 'generally'"]


def test_dollar_figures_from_any_tool_count_as_evidence():
    log = COMMUTE_15 + [{"name": "compare_to_area",
                         "result": "15 other tracked listings in 98107: median $2.62/sqft"}]
    assert check("At $2.82 per sqft it is above the area median of $2.62.", log=log) == ([], [])
