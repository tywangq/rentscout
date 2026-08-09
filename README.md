# RentScout

An autonomous housing-search agent: give it a search profile once and it runs
unattended every day — pulling new rental listings, deduplicating against what
it has already seen, deciding which candidates deserve deeper investigation,
and delivering a short ranked digest — while staying under a **hard dollar
budget enforced in code, not in the prompt**.

RentScout is the agent counterpart to [LeaseHound](https://github.com/tywangq/leasehound),
a fixed-pipeline lease scanner: same domain, opposite control-flow philosophy.
Together they argue one point — knowing when a problem needs a workflow and
when it needs a (bounded) agent.

**Status: M0** — agent skeleton, fixture data source, budget enforcement, and
scenario-replay tests. No live LLM calls yet; the full pipeline runs offline
against a deterministic rule-based model stand-in. See [SPEC.md](SPEC.md) for
the design and milestones.

## Try it (no API key needed)

```bash
uv sync
uv run pytest
uv run python -m rentscout replay \
  --scenario fixtures/scenarios/basic \
  --profile examples/profile.toml \
  --state /tmp/rentscout.db --out runs/
```

`replay` simulates three days of Seattle listings and writes one Markdown
digest per day to `runs/`, including each run's spend against its caps.

Or drive it interactively in the browser (stdlib server, still zero deps):

```bash
uv run python -m rentscout ui \
  --scenario fixtures/scenarios/basic \
  --profile examples/profile.toml \
  --state runs/ui-demo.db --out runs/
```

Open http://localhost:8777, step through the days, and thumbs-down a pick —
it will never be surfaced again, even if the listing later re-lists.

## Design highlights

- **Bounded autonomy.** Per-run dollar cap (counted from actual token usage),
  a per-run quota of "deep investigation" tool calls the agent must ration,
  and a monthly ledger cap that refuses to start runs. All in code.
- **Autonomy only where it pays.** Fetch, dedupe, and hard-constraint filtering
  are deterministic code; the LLM scores soft preferences and decides where to
  spend its investigation quota.
- **Listing text is hostile input.** Same injection posture as LeaseHound.
- **Every run leaves a trace** — decisions, tool calls, and cost persist to
  SQLite, so production runs double as an eval corpus.
