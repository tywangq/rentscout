"""CLI: replay fixture scenarios and record feedback.

Runs offline against RuleBasedLLM by default; --llm openai uses the real model
(needs OPENAI_API_KEY and the optional dependency: uv sync --extra openai).
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from .llm import LLMClient, OpenAIClient, RuleBasedLLM
from .pipeline import daily_run
from .profile import load_profile
from .sources.fixture import FixtureSource
from .state import Store
from .tools import build_registry


def _make_llm(args: argparse.Namespace) -> LLMClient:
    if args.llm == "openai":
        return OpenAIClient(model=args.model)
    return RuleBasedLLM()


def _run_day(args: argparse.Namespace, day: int) -> None:
    source = FixtureSource(args.scenario, day)
    run_date = (
        date.fromisoformat(source.start_date) + timedelta(days=day - 1)
    ).isoformat()
    profile, caps = load_profile(args.profile)
    store = Store(args.state)
    try:
        registry = build_registry(store, source.commutes())
        result = daily_run(
            source=source,
            store=store,
            profile=profile,
            caps=caps,
            llm=_make_llm(args),
            registry=registry,
            run_date=run_date,
            out_dir=args.out,
        )
        status = "HALTED" if result.halted else "ok"
        print(
            f"day {day} ({run_date}): {result.new_count} new, "
            f"{len(result.pick_ids)} picks, ${result.spent:.6f} spent [{status}]"
            f" -> {result.digest_path}"
        )
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="rentscout")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scenario", required=True)
    common.add_argument("--profile", required=True)
    common.add_argument("--state", required=True)
    common.add_argument("--out", default="runs")
    common.add_argument("--llm", choices=["fake", "openai"], default="fake")
    common.add_argument("--model", default="gpt-4.1-mini")

    run = sub.add_parser("run", parents=[common], help="run one simulated day")
    run.add_argument("--day", type=int, required=True)

    sub.add_parser("replay", parents=[common], help="run every day in the scenario")

    ui = sub.add_parser("ui", parents=[common], help="local web UI (demo mode)")
    ui.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", "8777"))
    )

    feedback = sub.add_parser("feedback", help="record a verdict on a listing")
    feedback.add_argument("--state", required=True)
    feedback.add_argument("--listing", required=True)
    feedback.add_argument("--verdict", choices=["up", "down"], required=True)
    feedback.add_argument("--reason", default="")

    args = parser.parse_args()
    if args.command == "run":
        _run_day(args, args.day)
    elif args.command == "replay":
        for day in FixtureSource.available_days(args.scenario):
            _run_day(args, day)
    elif args.command == "ui":
        from .ui import serve

        serve(args.scenario, args.profile, args.state, args.out, args.port)
    elif args.command == "feedback":
        store = Store(args.state)
        try:
            store.add_feedback(args.listing, args.verdict, args.reason)
            print(f"recorded: {args.verdict} on {args.listing}")
        finally:
            store.close()


if __name__ == "__main__":
    main()
