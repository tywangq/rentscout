from rentscout.ui import render_page


def test_hostile_listing_text_is_escaped():
    picks = [
        {
            "id": "test:xss",
            "address": "<script>alert(1)</script>",
            "neighborhood": "Capitol Hill",
            "price": 2000,
            "beds": 1.0,
            "baths": 1.0,
            "description": "<img src=x onerror=alert(2)>",
            "score": 7,
            "reason": "<b>injected</b>",
            "note": None,
        }
    ]
    page = render_page(
        profile_name="p",
        month_spend=0.0,
        monthly_cap=2.0,
        days_done=1,
        days_total=3,
        picks=picks,
        verdicts={},
        digest_text="<script>digest</script>",
        runs=[],
    )
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "<img src=x" not in page
    assert "<b>injected</b>" not in page


def test_run_button_hidden_when_scenario_done():
    page = render_page(
        profile_name="p",
        month_spend=0.0,
        monthly_cap=2.0,
        days_done=3,
        days_total=3,
        picks=[],
        verdicts={},
        digest_text="",
        runs=[],
    )
    assert "Run day" not in page
    assert "all days run" in page
