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
