"""Resolves a split method + raw inputs into an exact allocation in minor units.

A port of `frontend/js/lib/split.js`. Deterministic rounding: allocate the floor
of each weighted portion, then hand out the leftover minor units one at a time,
ordered by largest fractional part and then by member id ascending. The
allocation always reconciles exactly to the expense total.

Weights are carried as `Fraction` so the floors and remainders are exact rather
than float-rounded; the ordering rule is otherwise identical to the frontend's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Sequence


@dataclass(frozen=True)
class Share:
    member_id: str
    amount_minor: int


@dataclass(frozen=True)
class Allocation:
    ok: bool
    shares: list[Share]
    allocated_minor: int
    error: str | None


def _as_number(raw: object) -> float | None:
    """Mirror JS `Number(raw)`: non-numeric or non-finite becomes None (NaN)."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return 0.0
        try:
            value = float(text)
        except ValueError:
            return None
    else:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def compute_allocation(
    *,
    split_type: str,
    amount_minor: int,
    participants: Sequence[tuple[str, object]],
) -> Allocation:
    """Resolve `participants` (pairs of member id and raw input) into shares.

    For `exact`, raw is already minor units; for `percentage` raw is a percent
    0-100; for `share` raw is a positive weight; for `equal` raw is ignored.
    """
    if not participants:
        return Allocation(False, [], 0, "Select at least one participant")
    if not amount_minor > 0:
        return Allocation(False, [], 0, "Enter an amount greater than zero")

    ids = [member_id for member_id, _ in participants]
    raws = [raw for _, raw in participants]
    error: str | None = None

    if split_type == "equal":
        shares = _allocate_by_weights(amount_minor, [Fraction(1)] * len(ids), ids)

    elif split_type == "share":
        weights = [_as_number(raw) for raw in raws]
        if any(w is None or not w > 0 for w in weights):
            return Allocation(False, [], 0, "Every share must be a positive number")
        shares = _allocate_by_weights(
            amount_minor, [Fraction(w) for w in weights], ids
        )

    elif split_type == "percentage":
        pcts = [_as_number(raw) for raw in raws]
        if any(p is None or p < 0 for p in pcts):
            return Allocation(False, [], 0, "Every percentage must be zero or more")
        # Work in basis points (hundredths of a percent) to stay integer.
        basis_points = [_round_half_up(p * 100) for p in pcts]
        total_bp = sum(basis_points)
        shares = (
            [Share(member_id, 0) for member_id in ids]
            if total_bp == 0
            else _allocate_by_weights(
                amount_minor, [Fraction(bp) for bp in basis_points], ids
            )
        )
        if total_bp != 10000:
            error = (
                f"Percentages total {total_bp / 100:.2f}% — they must total 100%"
            )

    elif split_type == "exact":
        values = [_as_number(raw) for raw in raws]
        if any(v is None for v in values):
            return Allocation(False, [], 0, "Every amount must be zero or more")
        rounded = [_round_half_up(v) for v in values]
        if any(v < 0 for v in rounded):
            return Allocation(False, [], 0, "Every amount must be zero or more")
        shares = [Share(member_id, v) for member_id, v in zip(ids, rounded)]

    else:
        return Allocation(False, [], 0, f"Unknown split type: {split_type}")

    allocated = sum(s.amount_minor for s in shares)
    if error is None and allocated != amount_minor:
        error = "Allocation does not reconcile to the expense total"
    return Allocation(ok=error is None, shares=shares, allocated_minor=allocated, error=error)


def _round_half_up(value: float) -> int:
    """JS `Math.round` semantics: halves go toward positive infinity."""
    return math.floor(value + 0.5)


def _allocate_by_weights(
    amount_minor: int, weights: Sequence[Fraction], ids: Sequence[str]
) -> list[Share]:
    total = sum(weights, Fraction(0))
    if not total > 0:
        return [Share(member_id, 0) for member_id in ids]

    exact = [Fraction(amount_minor) * w / total for w in weights]
    floors = [math.floor(v) for v in exact]
    remainder = amount_minor - sum(floors)

    order = sorted(
        range(len(ids)),
        key=lambda i: (-(exact[i] - floors[i]), str(ids[i])),
    )

    out = list(floors)
    for i in order[:remainder]:
        out[i] += 1
    return [Share(member_id, out[i]) for i, member_id in enumerate(ids)]

