"""The public page: hostile listing text is escaped, and only top picks ship."""

from __future__ import annotations

import json

from rentscout.pipeline import MAX_PICKS
from rentscout.profile import load_profile
from rentscout.site import render_site

from conftest import PROFILE


def test_page_escapes_listing_and_model_text(store, run_day, tmp_path):
    result = run_day(1)
    lid = result.pick_ids[0]
    hostile = "<script>alert('x')</script>"
    store._conn.execute(
        "UPDATE listings SET address = ? WHERE id = ?", (hostile, lid)
    )
    row = store._conn.execute(
        "SELECT rowid, detail FROM decisions WHERE listing_id = ? AND action = 'investigated'",
        (lid,),
    ).fetchone()
    detail = json.loads(row["detail"]) | {"note": "<img src=x onerror=alert(1)>"}
    store._conn.execute("UPDATE decisions SET detail = ? WHERE rowid = ?",
                        (json.dumps(detail), row["rowid"]))
    store._conn.commit()
    profile, caps = load_profile(PROFILE)
    html = render_site(str(tmp_path / "state.db"), profile, caps, str(tmp_path))
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_page_shows_at_most_max_picks(store, run_day, tmp_path):
    run_day(1)
    profile, caps = load_profile(PROFILE)
    html = render_site(str(tmp_path / "state.db"), profile, caps, str(tmp_path))
    assert 0 < html.count("class='pick'") <= MAX_PICKS


def test_uninvestigated_picks_are_shown_and_labelled(store, run_day, tmp_path):
    run_day(1, investigations_per_run=1)
    profile, caps = load_profile(PROFILE)
    html = render_site(str(tmp_path / "state.db"), profile, caps, str(tmp_path))
    assert html.count("class='pick'") >= 2
    assert "Not investigated this run" in html


def test_funnel_counts_each_stage_from_the_trace(store, run_day, tmp_path):
    run_day(1)
    profile, caps = load_profile(PROFILE)
    html = render_site(str(tmp_path / "state.db"), profile, caps, str(tmp_path))
    # basic day 1: 7 new, 3 hard-filtered, 4 judged, all 4 investigated
    for fragment in ("<b>7</b> New listings today", "<b>4</b> Pass hard limits",
                     "<b>4</b> Judged by the model", "<b>4</b> Investigated with tools"):
        assert fragment in html


def test_breakdown_terms_sum_to_the_pipelines_score(store, run_day, tmp_path):
    import re

    run_day(1)
    profile, caps = load_profile(PROFILE)
    html = render_site(str(tmp_path / "state.db"), profile, caps, str(tmp_path))
    breakdowns = re.findall(r"<div class='breakdown'[^>]*>(.*?)</div>", html)
    scores = re.findall(r"<span class='score'>(\d+)/10</span>", html)
    assert breakdowns and len(breakdowns) == len(scores)
    for terms, score in zip(breakdowns, scores):
        total = re.search(r"= (\d+)(?: capped at (\d+))?", terms)
        assert int(total.group(2) or total.group(1)) == int(score)


def test_map_payload_cannot_close_its_script_tag():
    from rentscout.site import _map_html

    html = _map_html({"points": [{"rank": 1, "lat": 47.6, "lon": -122.3,
                                  "label": "</script><script>alert(1)</script>"}],
                      "anchor": None})
    assert "</script><script>alert(1)" not in html
