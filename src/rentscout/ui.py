"""Minimal local web UI — the M0 seed of the M3 demo-mode trace viewer.

Stdlib only, single-threaded, localhost. Step through simulated days, react to
picks with thumbs up/down, and watch rejection memory and budgets shape the
next run. Everything from the DB is HTML-escaped: listing text is hostile
input in the browser too, not just in prompts.
"""

from __future__ import annotations

import html
import json
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from .budget import MonthlyCapReached
from .llm import RuleBasedLLM
from .pipeline import daily_run
from .profile import load_profile
from .sources.fixture import FixtureSource
from .state import Store
from .tools import build_registry

_STYLE = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 720px;
       margin: 2rem auto; padding: 0 1rem; color: #1a1a2e; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.05rem; margin-top: 1.6rem; }
.bar { background: #f0f0f5; border-radius: 8px; padding: .7rem 1rem;
       display: flex; gap: 1.5rem; flex-wrap: wrap; font-size: .9rem; }
.card { border: 1px solid #ddd; border-radius: 10px; padding: .8rem 1rem;
        margin: .7rem 0; }
.card .head { display: flex; justify-content: space-between; gap: 1rem; }
.score { background: #1a1a2e; color: #fff; border-radius: 999px;
         padding: .1rem .6rem; font-size: .85rem; white-space: nowrap; }
.note { color: #444; font-size: .9rem; margin: .4rem 0 0; }
.desc { color: #777; font-size: .82rem; margin: .3rem 0 0; }
.badge { font-size: .85rem; padding: .15rem .5rem; border-radius: 6px; }
.badge.down { background: #fde8e8; color: #9b1c1c; }
.badge.up { background: #e6f6ec; color: #1c7c3f; }
button { border: 1px solid #bbb; background: #fff; border-radius: 8px;
         padding: .3rem .8rem; cursor: pointer; font-size: .9rem; }
button.primary { background: #1a1a2e; color: #fff; border: none;
                 padding: .5rem 1.2rem; font-size: 1rem; }
form.inline { display: inline; }
table { border-collapse: collapse; font-size: .88rem; }
td, th { padding: .25rem .8rem .25rem 0; text-align: left; }
details pre { background: #f7f7fa; padding: .8rem; border-radius: 8px;
              overflow-x: auto; font-size: .8rem; }
.notice { background: #fff7e0; border-radius: 8px; padding: .6rem 1rem; }
"""


def render_page(
    *,
    profile_name: str,
    month_spend: float,
    monthly_cap: float,
    days_done: int,
    days_total: int,
    picks: list[dict],
    verdicts: dict[str, str],
    digest_text: str,
    runs: list[dict],
    notice: str = "",
) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>RentScout</title><style>", _STYLE, "</style></head><body>",
        "<h1>RentScout <small style='color:#888;font-weight:normal'>",
        "demo mode · fixture data · offline model</small></h1>",
        f"<div class='bar'><span>profile: <b>{e(profile_name)}</b></span>",
        f"<span>month spend: <b>${month_spend:.4f}</b> / ${monthly_cap:.2f} cap</span>",
        f"<span>days run: <b>{days_done}</b> / {days_total}</span></div>",
    ]
    if notice:
        parts.append(f"<p class='notice'>{e(notice)}</p>")
    if days_done < days_total:
        parts.append(
            "<p><form class='inline' method='post' action='/run'>"
            f"<button class='primary'>&#9654; Run day {days_done + 1}</button>"
            "</form></p>"
        )
    else:
        parts.append("<p class='notice'>Scenario finished — all days run.</p>")

    if picks:
        parts.append(f"<h2>Top picks — day {days_done}</h2>")
    for pick in picks:
        verdict = verdicts.get(pick["id"])
        parts.append("<div class='card'><div class='head'><div>")
        parts.append(
            f"<b>${pick['price']}/mo · {pick['beds']:g}bd/{pick['baths']:g}ba · "
            f"{e(pick['neighborhood'])}</b><br>{e(pick['address'])}</div>"
        )
        parts.append(f"<span class='score'>{pick['score']}/10</span></div>")
        parts.append(f"<div class='note'>{e(pick['reason'])}</div>")
        note = pick["note"] or "not investigated this run"
        parts.append(f"<div class='note'>{e(note)}</div>")
        if pick["description"]:
            parts.append(f"<div class='desc'>&ldquo;{e(pick['description'][:140])}&rdquo;</div>")
        if verdict:
            label = "rejected — will never be shown again" if verdict == "down" else "liked"
            parts.append(f"<p><span class='badge {verdict}'>{label}</span></p>")
        else:
            parts.append(
                "<p><form class='inline' method='post' action='/feedback'>"
                f"<input type='hidden' name='listing_id' value='{e(pick['id'])}'>"
                "<button name='verdict' value='up'>&#128077;</button> "
                "<button name='verdict' value='down'>&#128078;</button></form></p>"
            )
        parts.append("</div>")

    if digest_text:
        parts.append(
            "<details><summary>Raw digest (what a daily email would say)</summary>"
            f"<pre>{e(digest_text)}</pre></details>"
        )

    if runs:
        parts.append("<h2>Run history</h2><table><tr><th>date</th>"
                     "<th>status</th><th>LLM spend</th></tr>")
        for run in runs:
            parts.append(
                f"<tr><td>{e(run['run_date'])}</td><td>{e(run['status'])}</td>"
                f"<td>${run['dollars'] or 0:.6f}</td></tr>"
            )
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)


class App:
    def __init__(
        self, scenario_dir: str, profile_path: str, state_path: str, out_dir: str
    ) -> None:
        self.scenario_dir = scenario_dir
        self.profile, self.caps = load_profile(profile_path)
        self.store = Store(state_path)
        self.out_dir = out_dir
        self.days = FixtureSource.available_days(scenario_dir)
        self.notice = ""

    def days_done(self) -> int:
        return len(
            [r for r in self.store.all_runs() if not r["status"].startswith("refused")]
        )

    def run_next(self) -> None:
        day = self.days_done() + 1
        if day > len(self.days):
            self.notice = "Scenario finished."
            return
        source = FixtureSource(self.scenario_dir, day)
        run_date = (
            date.fromisoformat(source.start_date) + timedelta(days=day - 1)
        ).isoformat()
        try:
            daily_run(
                source=source,
                store=self.store,
                profile=self.profile,
                caps=self.caps,
                llm=RuleBasedLLM(),
                registry=build_registry(self.store, source.commutes()),
                run_date=run_date,
                out_dir=self.out_dir,
            )
            self.notice = ""
        except MonthlyCapReached as exc:
            self.notice = f"Run refused: {exc}"

    def feedback(self, listing_id: str, verdict: str) -> None:
        if verdict in ("up", "down") and self.store.listing(listing_id):
            self.store.add_feedback(listing_id, verdict, "via ui")

    def page(self) -> str:
        latest = next(
            (
                r
                for r in reversed(self.store.all_runs())
                if not r["status"].startswith("refused")
            ),
            None,
        )
        picks, digest_text = [], ""
        if latest:
            picks = self._picks(latest["run_id"])
            digest = Path(latest["digest_path"] or "")
            digest_text = digest.read_text() if digest.is_file() else ""
        return render_page(
            profile_name=self.profile.name,
            month_spend=self.store.month_spend(_month_of(latest)),
            monthly_cap=self.caps.monthly_dollars,
            days_done=self.days_done(),
            days_total=len(self.days),
            picks=picks,
            verdicts=self.store.feedback_map(),
            digest_text=digest_text,
            runs=[dict(r) for r in self.store.all_runs()],
            notice=self.notice,
        )

    def _picks(self, run_id: str) -> list[dict]:
        triaged, notes = {}, {}
        for row in self.store.decisions(run_id):
            if row["action"] == "triaged":
                triaged[row["listing_id"]] = json.loads(row["detail"])
            elif row["action"] == "investigated":
                notes[row["listing_id"]] = json.loads(row["detail"])["note"]
        picks = []
        for listing_id, detail in triaged.items():
            if detail["score"] < self.caps.min_score_to_investigate:
                continue
            listing = self.store.listing(listing_id)
            if listing is None:
                continue
            picks.append(
                {
                    "id": listing_id,
                    "address": listing["address"],
                    "neighborhood": listing["neighborhood"],
                    "price": listing["price"],
                    "beds": listing["beds"],
                    "baths": listing["baths"],
                    "description": listing["description"],
                    "score": detail["score"],
                    "reason": detail["reason"],
                    "note": notes.get(listing_id),
                }
            )
        picks.sort(key=lambda p: (-p["score"], p["price"]))
        return picks


def _month_of(run_row) -> str:
    return run_row["run_date"][:7] if run_row else "0000-00"


def _make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            body = app.page().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(length).decode())
            if self.path == "/run":
                app.run_next()
            elif self.path == "/feedback":
                app.feedback(
                    form.get("listing_id", [""])[0], form.get("verdict", [""])[0]
                )
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *args) -> None:  # keep the console quiet
            pass

    return Handler


def serve(
    scenario_dir: str, profile_path: str, state_path: str, out_dir: str, port: int
) -> None:
    app = App(scenario_dir, profile_path, state_path, out_dir)
    server = HTTPServer(("127.0.0.1", port), _make_handler(app))
    print(f"RentScout UI: http://127.0.0.1:{port}")
    server.serve_forever()
