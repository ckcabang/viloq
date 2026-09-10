"""Derives net balances from persisted transactions.

A port of `frontend/js/lib/balances.js`. Balances are never stored; they are
always a pure function of expenses + payments.

    net_balance(member)
      = sum of expense totals the member paid
      - sum of the member's own allocated expense shares
      + payments the member made      (settling a debt raises you toward 0)
      - payments the member received  (being paid back lowers you toward 0)

Positive => the member is owed money. Negative => the member owes money.

Note: the spec's section 15 prose inverts the payment signs, but its own
section 9 settlement example (Bob -40 pays Alice +40 => both settled) confirms
the direction used here, and it is the direction the frontend already assumes.
"""

from __future__ import annotations

from typing import Sequence

from app.models import Expense, Member, Payment


def compute_balances(
    members: Sequence[Member],
    expenses: Sequence[Expense],
    payments: Sequence[Payment],
) -> list[dict[str, object]]:
    net: dict[str, int] = {m.id: 0 for m in members}

    def add(member_id: str, delta: int) -> None:
        net[member_id] = net.get(member_id, 0) + delta

    for expense in expenses:
        add(expense.payer_member_id, expense.amount_minor)
        for share in expense.shares:
            add(share.member_id, -share.amount_minor)

    for payment in payments:
        add(payment.payer_member_id, payment.amount_minor)
        add(payment.recipient_member_id, -payment.amount_minor)

    return [{"memberId": m.id, "netMinor": net.get(m.id, 0)} for m in members]
