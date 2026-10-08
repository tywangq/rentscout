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
from .routing import ORSCommute
from .sources.fixture import FixtureSource
from .sources.rentcast import RentCastError, RentCastSource
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


def _load_dotenv(path: str = ".env") -> None:
    """Read KEY=value lines into the environment without overriding it."""
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def _run_live(args: argparse.Namespace) -> None:
    """One real day: RentCast listings, today's date, the chosen model."""
    _load_dotenv()
    api_key = os.environ.get("RENTCAST_API_KEY")
    if not api_key:
        raise SystemExit("RENTCAST_API_KEY is not set (put it in .env)")
    run_date = date.today().isoformat()
    profile, caps = load_profile(args.profile)
    store = Store(args.state)
    try:
        source = RentCastSource(
            api_key=api_key,
            store=store,
            month=run_date[:7],
            monthly_cap=caps.rentcast_requests_per_month,
            min_beds=profile.min_beds,
            max_price=profile.max_price,
        )
        ors_key = os.environ.get("ORS_API_KEY")
        commute = (
            ORSCommute(
                api_key=ors_key,
                store=store,
                anchor=profile.commute_anchor,
                mode=profile.commute_mode,
            )
            if ors_key
            else {}
        )
        registry = build_registry(store, commute)
        try:
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
        except RentCastError as exc:
            raise SystemExit(f"run failed: {exc}")
        status = "HALTED" if result.halted else "ok"
        print(
            f"{run_date}: {result.new_count} new, {len(result.pick_ids)} picks, "
            f"${result.spent:.6f} spent, RentCast "
            f"{store.api_calls(run_date[:7], 'rentcast')}/"
            f"{caps.rentcast_requests_per_month} this month [{status}]"
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

    live = sub.add_parser("live", help="run today against real RentCast listings")
    live.add_argument("--profile", required=True)
    live.add_argument("--state", required=True)
    live.add_argument("--out", default="runs")
    live.add_argument("--llm", choices=["fake", "openai"], default="openai")
    live.add_argument("--model", default="gpt-4.1-mini")

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
    elif args.command == "live":
        _run_live(args)
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
