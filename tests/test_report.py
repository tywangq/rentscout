"""The monitoring report is built from the trace alone."""

from __future__ import annotations

from rentscout.profile import load_profile
from rentscout.report import build_report, render_markdown

from conftest import PROFILE


def test_report_has_one_row_per_run_from_the_trace(store, run_day, tmp_path):
    run_day(1)
    run_day(2)
    profile, _ = load_profile(PROFILE)
    rows, _ = build_report(str(tmp_path / "state.db"), profile)
    assert len(rows) == 2 and all(r.status == "ok" for r in rows)
    first = rows[0]
    assert first.triaged > 0 and first.investigated > 0
    assert first.commute_calls == first.tool_calls  # fixture agent only checks commute
    assert first.commute_unknown == 0
    text = render_markdown(rows, {})
    assert text.count("\n| 2026-08-0") == 2 and "Total spend" in text
