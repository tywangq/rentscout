"""Render the share card (og:image) for the live page, matching the portfolio's.

Same layout as tywangq.vercel.app's card: a window bar with the mark and name,
a teal eyebrow, a two-line claim, and three value / what / note / stack rows.
The figures are stable facts of the design, not today's numbers, because
LinkedIn caches a card for days; site.py versions the URL by the PNG's hash.

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

ROWS = [
    ("25/25", "injections held", "score set in code", "Python · OpenAI API · evals"),
    ("$0.05", "hard cap per run", "enforced in code", "budgets · quotas"),
    ("2–4", "tool calls a pick", "the model chooses", "function calling"),
]


def html(fonts: Path) -> str:
    mark = base64.b64encode((ROOT / "src/rentscout/data/favicon.png").read_bytes()).decode()
    face = lambda name, file: (
        f"@font-face{{font-family:{name};src:url('file://{fonts / file}')}}")
    rows = "".join(
        f"<div class='row'><div class='v'>{v}</div><div class='w'>{w}</div>"
        f"<div class='n'>{n}</div><div class='s'>{s}</div></div>"
        for v, w, n, s in ROWS
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{face('Geist', 'geist-regular.ttf')}{face('GeistBold', 'geist-bold.ttf')}{face('GeistMono', 'geist-mono.ttf')}
html,body{{margin:0;width:1200px;height:630px;background:#eef2f6;font-family:Geist}}
.card{{position:absolute;left:56px;top:56px;width:1088px;height:518px;background:#fff;
  border:2px solid #e2e8f0;border-radius:16px;overflow:hidden;box-sizing:border-box}}
.bar{{height:62px;background:#f6f8fa;border-bottom:2px solid #e2e8f0;display:flex;
  align-items:center;padding:0 24px 0 22px}}
.dot{{width:12px;height:12px;border-radius:6px;background:#e5eaf0;margin-right:10px}}
.bar img{{width:30px;height:30px;margin-left:22px}}
.name{{font-family:GeistBold;font-size:21px;color:#0f172a;margin-left:10px}}
.right{{margin-left:auto;font-size:17px;color:#94a3b8}}
.body{{padding:34px 40px 0}}
.eyebrow{{font-family:GeistBold;font-size:15px;letter-spacing:.12em;color:#0f766e}}
h1{{font-family:GeistBold;font-size:42px;line-height:1.18;color:#0f172a;margin:14px 0 0}}
.rule{{height:2px;background:#e2e8f0;margin-top:26px}}
.row{{display:flex;align-items:baseline;padding:18px 0;border-bottom:2px solid #f1f5f9}}
.row:last-child{{border-bottom:0}}
.v{{width:200px;font-family:GeistBold;font-size:24px;color:#0f766e}}
.w{{font-family:GeistBold;font-size:19px;color:#0f172a}}
.n{{margin-left:10px;font-size:17px;color:#94a3b8}}
.s{{margin-left:auto;font-family:GeistMono;font-size:14px;color:#94a3b8}}
</style></head><body><div class="card">
<div class="bar"><div class="dot"></div><div class="dot"></div><div class="dot"></div>
<img src="data:image/png;base64,{mark}"><div class="name">RentScout</div>
<div class="right">live &middot; rebuilt every morning</div></div>
<div class="body"><div class="eyebrow">BOUNDED AGENT &middot; SEATTLE RENTALS</div>
<h1>Scouts Seattle rentals every morning,<br>on a budget it can&rsquo;t talk its way out of.</h1>
<div class="rule"></div>{rows}</div></div></body></html>"""


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
