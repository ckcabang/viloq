"""Settlement suggestions — recommendations only, never payment records.

A port of `frontend/js/lib/settle.js`.
"""

from __future__ import annotations

from typing import Sequence

from app.models import Expense, Member, Payment


def minimized_transfers(
    balances: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Greedily match the largest creditor with the largest debtor.

    Produces at most (members - 1) transfers.
    """
    creditors = sorted(
        ({**b} for b in balances if b["netMinor"] > 0),
        key=lambda b: (-b["netMinor"], b["memberId"]),
    )
    debtors = sorted(
        ({**b} for b in balances if b["netMinor"] < 0),
        key=lambda b: (b["netMinor"], b["memberId"]),
    )

    transfers: list[dict[str, object]] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(-debtors[i]["netMinor"], creditors[j]["netMinor"])
        if amount > 0:
            transfers.append(
                {
                    "fromMemberId": debtors[i]["memberId"],
                    "toMemberId": creditors[j]["memberId"],
                    "amountMinor": amount,
                }
            )
            debtors[i]["netMinor"] += amount
            creditors[j]["netMinor"] -= amount
        if debtors[i]["netMinor"] == 0:
            i += 1
        if creditors[j]["netMinor"] == 0:
            j += 1
    return transfers


def relationship_preserving_transfers(
    members: Sequence[Member],
    expenses: Sequence[Expense],
    payments: Sequence[Payment],
) -> list[dict[str, object]]:
    """Keep the real debtor -> creditor pairs that arose from actual expenses.

    Nets out mutual debt and applies recorded payments, instead of collapsing
    everyone onto a few hubs.
    """
    # owed[(debtor, creditor)] = minor units the debtor owes the creditor
    owed: dict[tuple[str, str], int] = {}

    def bump(debtor: str, creditor: str, delta: int) -> None:
        key = (debtor, creditor)
        owed[key] = owed.get(key, 0) + delta

    for expense in expenses:
        for share in expense.shares:
            if share.member_id != expense.payer_member_id:
                bump(share.member_id, expense.payer_member_id, share.amount_minor)

    for payment in payments:
        # Paying someone reduces what you owe them.
        bump(payment.payer_member_id, payment.recipient_member_id, -payment.amount_minor)

    ids = [m.id for m in members]
    transfers: list[dict[str, object]] = []
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            diff = owed.get((a, b), 0) - owed.get((b, a), 0)
            if diff > 0:
                transfers.append(
                    {"fromMemberId": a, "toMemberId": b, "amountMinor": diff}
                )
            elif diff < 0:
                transfers.append(
                    {"fromMemberId": b, "toMemberId": a, "amountMinor": -diff}
                )

    return sorted(transfers, key=lambda t: (t["fromMemberId"], t["toMemberId"]))
