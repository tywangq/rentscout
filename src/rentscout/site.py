"""Static public page: today's picks, the agent's trace, run history, evals.

Published by the daily workflow to GitHub Pages. Two rules shape it:

- Only the top picks are shown (at most MAX_PICKS), never the full listing
  feed or the database. RentCast's API terms (sec. 2(iii)) require reasonable
  measures against third parties scraping the data through what we display.
- Every string that came from a listing or a model is HTML-escaped. Listing
  text is hostile input; the page must not become an injection surface.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, timedelta
from html import escape
from urllib.parse import quote, quote_plus
from pathlib import Path

from .pipeline import MAX_PICKS
from .profile import BudgetCaps, SearchProfile
from .report import build_report
from .state import Store
from .triage import score_parts

REPO = "https://github.com/tywangq/rentscout"
PAGE_URL = "https://tywangq.github.io/rentscout/"
SOCIAL_TITLE = "RentScout \u2014 daily rental-search agent"
SOCIAL_DESCRIPTION = (
    "A bounded agent that scouts Seattle rentals every morning on live data: the "
    "model judges and picks its tools, code sets the score and the budget."
)


def _card_version() -> str:
    """Hash of the card PNG, so a new card means a new og:image URL (LinkedIn
    caches by URL; the portfolio card stayed stale until it did this)."""
    from importlib import resources

    data = resources.files("rentscout.data").joinpath("card.png").read_bytes()
    return hashlib.sha256(data).hexdigest()[:10]


def _latest_ok_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE status IN ('ok', 'halted')"
        # run_date first: started_at is wall-clock, and two runs in one second
        # (a replay, a test) would otherwise pick an arbitrary "latest".
        " ORDER BY run_date DESC, started_at DESC, rowid DESC LIMIT 1"
    ).fetchone()


def _picks(conn: sqlite3.Connection, run_id: str, min_score: int) -> list[dict]:
    """Every listing scoring at or above the threshold, investigated or not:
    a pick the budget left uninvestigated is still a pick, labelled as such."""
    triaged, investigated = {}, {}
    for row in conn.execute(
        "SELECT listing_id, action, detail FROM decisions WHERE run_id = ?", (run_id,)
    ):
        if row["action"] == "triaged":
            triaged[row["listing_id"]] = json.loads(row["detail"])
        elif row["action"] == "investigated":
            investigated[row["listing_id"]] = json.loads(row["detail"])
    picks = []
    for lid, tri in triaged.items():
        if tri.get("score", 0) < min_score:
            continue
        listing = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
        if listing is None:
            continue
        picks.append({"listing": listing, "triage": tri,
                      "investigation": investigated.get(lid)})
    picks.sort(key=lambda p: (-p["triage"].get("score", 0), p["listing"]["price"]))
    return picks[:MAX_PICKS]


def _recent_picks(
    conn: sqlite3.Connection, latest: sqlite3.Row, min_score: int, exclude: set[str],
    days: int = 7,
) -> list[dict]:
    """Investigated picks from the past week's earlier runs that are still listed.

    After the cold start a day brings a dozen new listings, so 'today' alone can
    be empty; a renter still wants the good ones from Tuesday that are not gone.
    """
    since = (date.fromisoformat(latest["run_date"]) - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        "SELECT d.run_id, d.listing_id, d.action, d.detail FROM decisions d"
        " JOIN runs r ON r.run_id = d.run_id"
        " WHERE r.run_date >= ? AND d.run_id != ? AND d.action IN ('triaged', 'investigated')"
        " ORDER BY r.run_date, r.started_at, d.rowid",
        (since, latest["run_id"]),
    ).fetchall()
    triaged, investigated = {}, {}
    for row in rows:  # later runs overwrite earlier ones for the same listing
        (triaged if row["action"] == "triaged" else investigated)[row["listing_id"]] = (
            json.loads(row["detail"]))
    picks = []
    for lid, inv in investigated.items():
        tri = triaged.get(lid, {})
        if lid in exclude or tri.get("score", 0) < min_score:
            continue
        listing = conn.execute(
            "SELECT * FROM listings WHERE id = ? AND status = 'active'", (lid,)
        ).fetchone()
        if listing is not None:
            picks.append({"listing": listing, "triage": tri, "investigation": inv})
    picks.sort(key=lambda p: (-p["triage"].get("score", 0), p["listing"]["price"]))
    return picks[:MAX_PICKS]


def _month_spend(conn: sqlite3.Connection, month: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(dollars), 0) AS s FROM spend_ledger WHERE run_date LIKE ?",
        (f"{month}%",),
    ).fetchone()
    return row["s"]


def _api_calls(conn: sqlite3.Connection, month: str) -> int:
    row = conn.execute(
        "SELECT calls FROM api_usage WHERE month = ? AND api = 'rentcast'", (month,)
    ).fetchone()
    return row["calls"] if row else 0


def _eval_summary(eval_dir: Path) -> str:
    def load(name):
        path = eval_dir / name
        return json.loads(path.read_text()) if path.exists() else None

    before, after = load("injection_results_before_fix.json"), load("injection_results.json")
    if not after:
        return "<p>No evaluation results yet.</p>"
    rows = []
    for label, d in (("Model picks the score", before), ("Code computes the score", after)):
        if not d:
            continue
        per = " ".join(
            f"<span class='chip'>{escape(k)} {v}/{d['reps']}</span>"
            for k, v in d["by_payload"].items()
        )
        rows.append(
            f"<tr><td>{escape(label)}</td><td class='num'>{d['held']}/{d['total']}</td>"
            f"<td>{per}</td><td>{escape(d['model'])}, {escape(d['date'])}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Triage design</th><th class='num'>Held</th>"
        "<th>By payload</th><th>Run</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
        "<p class='muted'>Five hostile payloads hidden in listing text, each paired with "
        "an identical control listing, repeated on fresh state. Held means the injected "
        "listing was treated like its control.</p>"
    )


def _breakdown_html(profile: SearchProfile, row: sqlite3.Row, verdicts: dict) -> str:
    """The score's terms as the pipeline computed them (triage.score_parts)."""
    parts = score_parts(profile, Store._row_to_listing(row), verdicts)
    total = sum(d for _, d in parts)
    terms = "".join(
        f"<span class='term {'neg' if d < 0 else 'pos'}'>{'+' if d > 0 and i else ''}"
        f"{'−' if d < 0 else ''}{abs(d)} {escape(label)}</span>"
        for i, (label, d) in enumerate(parts)
    )
    capped = f" capped at {max(0, min(10, total))}" if total != max(0, min(10, total)) else ""
    return (f"<div class='breakdown' title='computed in code, not by the model'>"
            f"{terms}<span class='term total'>= {total}{capped}</span></div>")


_MODE_WORD = {"bike": "bike", "car": "car", "walk": "foot", None: "bike"}


def _plain_step(call: dict) -> str:
    """One tool call as a sentence a non-engineer can read; raw calls stay below."""
    name, args = call.get("name", ""), call.get("arguments", {}) or {}
    result = str(call.get("result", ""))
    if name == "commute_time":
        how = _MODE_WORD.get(args.get("mode"), args.get("mode") or "bike")
        return f"Checked the commute by {escape(how)}: <b>{escape(result)}</b>"
    if name == "compare_to_area":
        # Drop the parenthetical scope note; keep the comparison.
        short = re.sub(r" \(all within[^)]*\)", "", result)
        return f"Compared the price with the area: {escape(short)}"
    if name == "price_history":
        return f"Looked up the price history: {escape(result)}"
    return f"{escape(name)}: {escape(result)}"


def _pick_html(p: dict, rank: int, profile: SearchProfile) -> str:
    l, tri, inv = p["listing"], p["triage"], p["investigation"]
    if inv is None:
        inv = {"note": "Not investigated this run: the budget or tool quota ran out "
                       "first. Score and verdicts are from triage only.",
               "tool_log": []}
    attrs = json.loads(l["attributes"] or "{}") if "attributes" in l.keys() else {}
    facts = [f"${l['price']:,}/mo", f"{l['beds']:g} bd / {l['baths']:g} ba"]
    if l["sqft"]:
        facts.append(f"{l['sqft']} sqft")
    if attrs.get("days_on_market") is not None:
        days = attrs["days_on_market"]
        facts.append(f"{days} day{'s' * (days != 1)} listed")
    mark = {"yes": "\u2713", "no": "\u2717", "unknown": "?"}
    verdicts = "".join(
        # "label: detail" preferences show the label; the full text is the tooltip.
        f"<span class='chip v-{escape(v)}' title='{escape(k)}: {escape(v)}'>"
        f"{mark.get(v, '?')} {escape(k.split(':', 1)[0])}</span>"
        for k, v in sorted(tri.get("verdicts", {}).items())
    )
    log = inv.get("tool_log", [])
    steps = "".join(f"<li>{_plain_step(c)}</li>" for c in log) or "<li>no lookups made</li>"
    raw = "".join(
        f"<li><code>{escape(c.get('name', ''))}({escape(json.dumps(c.get('arguments', {})))})</code>"
        f" &rarr; {escape(str(c.get('result', '')))}</li>"
        for c in log
    )
    return (
        f"<article class='pick' id='pick-{rank}'>"
        f"<header><h3><span class='rank'>{rank}</span>{escape(l['address'])}</h3>"
        f"<span class='score'>{escape(str(tri.get('score', '?')))}/10</span></header>"
        f"<p class='facts'>{escape(l['neighborhood'] or '')} &middot; "
        f"{escape(' · '.join(facts))} &middot; "
        f"<a href='{escape(l['url'])}' rel='noopener nofollow'>map</a> &middot; "
        # RentCast returns no listing URL or photos; a search on the address
        # finds the listing itself on whichever site is carrying it.
        f"find the listing: <a href='https://www.zillow.com/homes/{quote(l['address'])}_rb/' "
        f"rel='noopener nofollow'>Zillow</a> or "
        f"<a href='https://www.google.com/search?q={quote_plus(l['address'] + ' for rent')}' "
        f"rel='noopener nofollow'>search</a></p>"
        f"<div class='chips'>{verdicts}</div>"
        f"{_breakdown_html(profile, l, tri.get('verdicts', {}))}"
        f"<p>{escape(inv.get('note', ''))}</p>"
        f"<details><summary>What the agent checked ({len(log)} "
        f"lookup{'s' * (len(log) != 1)})</summary><ul class='steps'>{steps}</ul>"
        + (f"<details class='raw'><summary>raw tool calls</summary><ul>{raw}</ul></details>"
           if raw else "")
        + "</details>"
        "</article>"
    )


def _funnel(conn: sqlite3.Connection, run_id: str, min_score: int) -> list[tuple[str, int, str]]:
    """What the agent did with today's new listings, stage by stage."""
    rows = conn.execute(
        "SELECT action, detail FROM decisions WHERE run_id = ? AND listing_id IS NOT NULL",
        (run_id,),
    ).fetchall()
    count = lambda a: sum(1 for r in rows if r["action"] == a)
    triaged = [json.loads(r["detail"]) for r in rows if r["action"] == "triaged"]
    hard, suppressed = count("hard_filtered"), count("suppressed_rejected")
    capped = count("skipped_triage_cap")
    new = hard + suppressed + capped + len(triaged)
    return [
        ("New listings today", new, "from RentCast, after dedupe against everything seen before"),
        ("Pass hard limits", new - hard - suppressed, "price, beds, size, neighborhood; in code"),
        ("Judged by the model", len(triaged), "freshest first, capped so triage cannot eat the budget"),
        ("Score high enough", sum(1 for t in triaged if t.get("score", 0) >= min_score),
         "score computed in code from the model's verdicts"),
        ("Investigated with tools", count("investigated"), "the model chose which lookups to spend quota on"),
    ]


def _map_points(
    picks: list[dict], conn: sqlite3.Connection, anchor: str, label: str = ""
) -> dict | None:
    points = []
    for i, p in enumerate(picks, 1):
        attrs = json.loads(p["listing"]["attributes"] or "{}")
        if attrs.get("lat") is None or attrs.get("lon") is None:
            continue
        points.append({"rank": i, "lat": attrs["lat"], "lon": attrs["lon"],
                       "label": f"{p['listing']['address']} · ${p['listing']['price']:,}"})
    if not points:
        return None
    row = conn.execute("SELECT lat, lon FROM geocode_cache WHERE address = ?", (anchor,)).fetchone()
    return {"points": points,
            "anchor": {"lat": row["lat"], "lon": row["lon"], "label": label or anchor} if row else None}


def _map_html(data: dict | None) -> str:
    if not data:
        return ""
    # json.dumps output, with "<" escaped so listing text cannot close the script tag.
    payload = json.dumps(data).replace("<", "\\u003c")
    return f"""<div id="map" role="img" aria-label="Map of today's picks"></div>
<p class="muted">Numbers match the picks below; the star is where the renter commutes to.
Tiles &copy; OpenStreetMap contributors.</p>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
(function () {{
  var d = {payload};
  if (!window.L) return;
  var map = L.map("map", {{ scrollWheelZoom: false }});
  L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
    {{ maxZoom: 18, attribution: "&copy; OpenStreetMap contributors" }}).addTo(map);
  var bounds = [];
  d.points.forEach(function (p) {{
    var icon = L.divIcon({{ className: "pin", html: String(p.rank), iconSize: [26, 26] }});
    var m = L.marker([p.lat, p.lon], {{ icon: icon, title: p.label }}).addTo(map);
    m.on("click", function () {{
      var el = document.getElementById("pick-" + p.rank);
      if (el) {{ el.scrollIntoView({{ behavior: "smooth", block: "start" }}); el.classList.add("flash");
        setTimeout(function () {{ el.classList.remove("flash"); }}, 1200); }}
    }});
    bounds.push([p.lat, p.lon]);
  }});
  if (d.anchor) {{
    L.marker([d.anchor.lat, d.anchor.lon], {{ icon: L.divIcon({{ className: "pin anchor",
      html: "\u2605", iconSize: [26, 26] }}), title: d.anchor.label }}).addTo(map);
    bounds.push([d.anchor.lat, d.anchor.lon]);
  }}
  map.fitBounds(bounds, {{ padding: [24, 24] }});
}})();
</script>"""


def _funnel_html(stages: list[tuple[str, int, str]]) -> str:
    top = max((n for _, n, _ in stages), default=0) or 1
    bars = "".join(
        f"<div class='stage'><div class='stage-label'><b>{n}</b> {escape(label)}"
        f"<span class='muted'> · {escape(why)}</span></div>"
        f"<div class='bar'><span style='width:{max(2, round(100 * n / top))}%'></span></div></div>"
        for label, n, why in stages
    )
    return f"<div class='funnel'>{bars}</div>"


def render_site(
    db_path: str, profile: SearchProfile, caps: BudgetCaps, eval_dir: str = "evaluation"
) -> str:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _latest_ok_run(conn)
    month = (run["run_date"] if run else date.today().isoformat())[:7]
    picks = _picks(conn, run["run_id"], caps.min_score_to_investigate) if run else []
    earlier = _recent_picks(
        conn, run, caps.min_score_to_investigate, {p["listing"]["id"] for p in picks}
    ) if run else []
    funnel = _funnel(conn, run["run_id"], caps.min_score_to_investigate) if run else []
    map_data = _map_points(picks + earlier, conn, profile.commute_anchor, profile.commute_anchor_label)
    spend, calls = _month_spend(conn, month), _api_calls(conn, month)
    conn.close()
    rows, _ = build_report(db_path, profile)

    history = "".join(
        f"<tr><td>{escape(r.run_date)}</td><td>{escape(r.status[:40])}</td>"
        f"<td class='num'>{r.fresh}</td><td class='num'>{r.triaged}</td>"
        f"<td class='num'>{r.investigated}</td><td class='num'>{r.tool_calls}</td>"
        f"<td class='num'>{r.commute_unknown}/{r.commute_calls}</td>"
        f"<td class='num'>${r.spent:.4f}</td>"
        f"<td class='num'>{r.unsourced_notes}</td><td class='num'>{r.hedged_notes}</td></tr>"
        for r in reversed(rows[-14:])
    )
    picks_html = "".join(_pick_html(p, i, profile) for i, p in enumerate(picks, 1)) or (
        "<p>No new picks today; see the ones still available below.</p>"
        if earlier else "<p>No picks in the latest run.</p>")
    earlier_html = "".join(
        _pick_html(p, i, profile) for i, p in enumerate(earlier, len(picks) + 1))
    run_line = (
        f"Latest run {escape(run['run_date'])}, status {escape(run['status'])}, "
        f"${run['dollars'] or 0:.4f} spent." if run else "No completed run yet."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SOCIAL_TITLE}</title>
<meta name="description" content="{escape(SOCIAL_DESCRIPTION)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{PAGE_URL}">
<meta property="og:title" content="{SOCIAL_TITLE}">
<meta property="og:description" content="{escape(SOCIAL_DESCRIPTION)}">
<meta property="og:image" content="{PAGE_URL}card.png?v={_card_version()}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/png" href="favicon.png">
<style>
:root {{ --bg:#fbfaf8; --fg:#1d1d1b; --muted:#6b6a66; --card:#fff; --line:#e4e1db;
  --accent:#0f766e; --yes:#ccfbf1; --no:#f6dede; --unk:#ecebe7; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#161615; --fg:#ecebe7; --muted:#a3a19b; --card:#1f1f1d; --line:#33322f;
  --accent:#5eead4; --yes:#134e4a; --no:#432626; --unk:#2c2b29; }} }}
body {{ background:var(--bg); color:var(--fg); margin:0;
  font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
main {{ max-width:880px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ font-size:19px; margin:36px 0 10px; }}
h3 {{ font-size:16px; margin:0; }} .muted, .facts {{ color:var(--muted); }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }}
.stat {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; }}
.stat b {{ display:block; font-size:18px; }}
.funnel {{ display:grid; gap:10px; margin:8px 0 4px; }}
.stage-label {{ font-size:14px; margin-bottom:3px; }}
.stage-label b {{ font-size:16px; font-variant-numeric:tabular-nums; }}
.bar {{ height:10px; border-radius:5px; background:var(--unk); overflow:hidden; }}
.bar span {{ display:block; height:100%; background:var(--accent); border-radius:5px; }}
#map {{ height:340px; border-radius:10px; border:1px solid var(--line); margin:6px 0; }}
.pin {{ background:var(--accent); color:#fff; border-radius:50%; font:600 13px/26px sans-serif;
  text-align:center; box-shadow:0 1px 3px rgba(0,0,0,.35); }}
.pin.anchor {{ background:#1d1d1b; }}
.live {{ font-size:12px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
  color:var(--accent); border:1px solid var(--accent); border-radius:999px; padding:2px 8px;
  vertical-align:middle; }}
.rank {{ display:inline-block; min-width:22px; height:22px; margin-right:8px; border-radius:50%;
  background:var(--accent); color:var(--bg); font-size:12px; line-height:22px; text-align:center; }}
.breakdown {{ display:flex; flex-wrap:wrap; gap:4px; margin:4px 0 8px; font-size:12px; }}
.term {{ padding:1px 7px; border-radius:4px; border:1px solid var(--line); font-variant-numeric:tabular-nums; }}
.term.neg {{ color:#b42318; }} .term.total {{ font-weight:600; border-color:var(--accent); }}
.flash {{ outline:2px solid var(--accent); }}
.pick {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px; margin:12px 0; }}
.pick header {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
.score {{ font-weight:600; color:var(--accent); white-space:nowrap; }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }}
.chip {{ font-size:12px; padding:2px 8px; border-radius:999px; background:var(--unk); }}
.v-yes {{ background:var(--yes); }} .v-no {{ background:var(--no); }}
details {{ margin-top:6px; }}
.steps {{ margin:6px 0; padding-left:18px; }} .steps li {{ margin:3px 0; }}
details.raw {{ margin:4px 0 0 18px; font-size:12px; color:var(--muted); }}
.chip.v-no {{ text-decoration:none; }} code {{ font-size:12.5px; word-break:break-word; }}
.table-wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
a {{ color:var(--accent); }}
</style></head><body><main>
<h1>\U0001F415\u200d\U0001F9BA RentScout <span class="live">live</span></h1>
<p class="muted">A bounded autonomous agent searching Seattle rentals once a day on real
listings (RentCast), real routing (OpenRouteService) and a real model, under budgets
enforced in code. <a href="{REPO}">Source</a>.</p>
<p>{run_line}</p>
<div class="stats">
  <div class="stat"><b>${spend:.3f} / ${caps.monthly_dollars:.2f}</b>model spend this month</div>
  <div class="stat"><b>${caps.per_run_dollars:.2f}</b>hard cap per run</div>
  <div class="stat"><b>{calls} / {caps.rentcast_requests_per_month}</b>RentCast requests this month</div>
</div>
<h2>What the agent did today</h2>
{_funnel_html(funnel) if funnel else "<p>No completed run yet.</p>"}
<h2>Where they are</h2>
{_map_html(map_data) or "<p class='muted'>No coordinates for today's picks.</p>"}
<h2>Today's picks</h2>
<p class="muted">Profile: {escape(profile.name)}, up to ${profile.max_price:,}, commute to
{escape(profile.commute_anchor_label or profile.commute_anchor)}, by bike, car or on foot as the
agent chooses (the routing service has no transit; car times assume no traffic).
The model answers yes / no / unknown per preference; the score is computed in code.</p>
{picks_html}
{"<h2>Still available from this week</h2><p class='muted'>Picked on an earlier day and not delisted since; numbers continue on the map.</p>" + earlier_html if earlier else ""}
<h2>Run history</h2>
<div class="table-wrap"><table><thead><tr><th>Date</th><th>Status</th>
<th class="num">Listings</th><th class="num">Triaged</th><th class="num">Investigated</th>
<th class="num">Tool calls</th><th class="num">Commute unknown</th><th class="num">Spend</th>
<th class="num">Unsourced notes</th><th class="num">Hedged notes</th></tr></thead>
<tbody>{history}</tbody></table></div>
<p class="muted">Unsourced notes: numbers no listing field or tool result supports.
Hedged notes: guessing language such as "generally" or "likely". Both are checked
deterministically from the trace.</p>
<h2>Injection evals</h2>
<div class="table-wrap">{_eval_summary(Path(eval_dir))}</div>
</main></body></html>
"""
