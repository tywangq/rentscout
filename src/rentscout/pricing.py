"""Model pricing, USD per 1M tokens (input, output).

PLACEHOLDER values — verify against current provider pricing at M1, before the
first real API call. Fake models are priced like the real triage model so
budget math in tests exercises the same code path as production.
"""

from __future__ import annotations

PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "rule-based-fake": (0.40, 1.60),
    "scripted-fake": (0.40, 1.60),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICING[model]
    return (input_tokens * inp + output_tokens * out) / 1_000_000
