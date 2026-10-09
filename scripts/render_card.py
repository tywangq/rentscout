"""Render the share card (og:image) for the live page, matching the portfolio's.

Built like LeaseHound's card rather than the portfolio's: the portfolio card is
the person (a claim and three projects), a project card is the product at work.
LeaseHound shows one flagged clause and its statute; this shows one real pick
with the agent's checks and the score's terms, then three figures. The pick is
a fixed example from the live page, since LinkedIn caches a card for days;
site.py versions the og:image URL by the PNG's hash.

Needs Google Chrome and the Geist fonts (the portfolio repo ships them):

    python scripts/render_card.py --fonts ~/projects/portfolio/src/assets

Writes src/rentscout/data/card.png. The PNG is committed; CI does not run this.
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "rentscout" / "data" / "card.png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# One real pick from a live run on 2026-10-08 (verdicts, lookups and score as recorded), the way LeaseHound's card shows
# one real flagged clause: the product doing its job, not a slogan.
PICK = {
    "address": "4259 8th Ave NE, Apt 4",
    "facts": "University District · $1,650/mo · 2 bd · 650 sq ft · listed 1 day",
    "chips": ["big enough", "good value", "apartment or condo", "fresh listing"],
    "steps": [
        ("Commute", "20 min by bike · 12 min by car"),
        ("Compared with the area", "$2.54/sq ft, 15% below the 98105 median"),
    ],
    "terms": ["3 base", "+2 price", "+1 area", "+4 preferences", "= 10"],
}
STATS = [("25/25", "injections held"), ("2\u20134", "tool calls a pick"), ("$0.05", "hard cap per run")]


def html(fonts: Path) -> str:
    mark = base64.b64encode((ROOT / "src/rentscout/data/favicon.png").read_bytes()).decode()
    face = lambda name, file: (
        f"@font-face{{font-family:{name};src:url('file://{fonts / file}')}}")
    chips = "".join(f"<span class='chip'><i>\u2713</i>{c}</span>" for c in PICK["chips"])
    steps = "".join(f"<div class='step'><b>{k}</b> {v}</div>" for k, v in PICK["steps"])
    terms = "".join(
        f"<span class='term{' total' if t.startswith('=') else ''}'>{t}</span>"
        for t in PICK["terms"])
    stats = "".join(f"<div class='stat'><b>{v}</b><span>{w}</span></div>" for v, w in STATS)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{face('Geist', 'geist-regular.ttf')}{face('GeistBold', 'geist-bold.ttf')}{face('GeistMono', 'geist-mono.ttf')}
html,body{{margin:0;width:1200px;height:630px;background:#eef2f6;font-family:Geist;color:#0f172a}}
.card{{position:absolute;left:56px;top:56px;width:1088px;height:518px;background:#fff;
  border:2px solid #e2e8f0;border-radius:16px;overflow:hidden;box-sizing:border-box}}
.bar{{height:62px;background:#f6f8fa;border-bottom:2px solid #e2e8f0;display:flex;
  align-items:center;padding:0 24px 0 22px}}
.dot{{width:12px;height:12px;border-radius:6px;background:#e5eaf0;margin-right:10px}}
.bar img{{width:30px;height:30px;margin-left:22px}}
.name{{font-family:GeistBold;font-size:21px;margin-left:10px}}
.right{{margin-left:auto;font-family:GeistMono;font-size:15px;color:#94a3b8}}
.body{{padding:28px 38px 0}}
.head{{display:flex;align-items:baseline;gap:14px}}
.head h1{{font-family:GeistBold;font-size:26px;margin:0}}
.head span{{font-family:GeistMono;font-size:16px;color:#94a3b8}}
.chips{{display:flex;gap:10px;margin:16px 0 18px}}
.chip{{font-family:GeistBold;font-size:16px;padding:7px 14px;border-radius:999px;
  border:2px solid #e2e8f0;background:#f8fafc}}
.chip i{{font-style:normal;color:#0f766e;margin-right:7px}}
.pick{{border:2px solid #99f6e4;border-left:6px solid #0f766e;border-radius:10px;
  background:#f0fdfa;padding:16px 22px}}
.addr{{display:flex;justify-content:space-between;align-items:baseline}}
.addr b{{font-family:GeistBold;font-size:24px}}
.addr em{{font-style:normal;font-family:GeistBold;font-size:24px;color:#0f766e}}
.facts{{font-size:16px;color:#64748b;margin-top:4px}}
.step{{font-size:18px;margin-top:9px}} .step b{{font-family:GeistBold;color:#0f766e;margin-right:6px}}
.terms{{display:flex;gap:6px;margin-top:12px}}
.term{{font-family:GeistMono;font-size:13px;padding:3px 9px;border:1.5px solid #cbd5e1;
  border-radius:5px;background:#fff;color:#334155}}
.term.total{{border-color:#0f766e;color:#0f766e;font-weight:700}}
.stats{{display:flex;gap:44px;border-top:2px solid #e2e8f0;margin-top:20px;padding-top:16px}}
.stat b{{font-family:GeistBold;font-size:28px;color:#0f766e;margin-right:10px}}
.stat span{{font-size:17px;color:#94a3b8}}
</style></head><body><div class="card">
<div class="bar"><div class="dot"></div><div class="dot"></div><div class="dot"></div>
<img src="data:image/png;base64,{mark}"><div class="name">RentScout</div>
<div class="right">Seattle &middot; live listings &middot; rebuilt every morning</div></div>
<div class="body">
<div class="head"><h1>Today&rsquo;s pick</h1><span>judged by the model &middot; scored in code</span></div>
<div class="chips">{chips}</div>
<div class="pick"><div class="addr"><b>{PICK['address']}</b><em>10/10</em></div>
<div class="facts">{PICK['facts']}</div>{steps}<div class="terms">{terms}</div></div>
<div class="stats">{stats}</div>
</div></div></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fonts", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(html(args.fonts.expanduser()))
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--window-size=1200,630", "--force-device-scale-factor=1",
             "--virtual-time-budget=3000", f"--screenshot={OUT}", page.as_uri()],
            check=True, capture_output=True,
        )
    print(f"card -> {OUT}")


if __name__ == "__main__":
    main()
