// Derives net balances from persisted transactions. Balances are never stored;
// they are always a pure function of expenses + payments.
//
// net_balance(member)
//   = sum of expense totals the member paid
//   - sum of the member's own allocated expense shares
//   + payments the member made      (settling a debt raises your balance toward 0)
//   - payments the member received  (being paid back lowers your balance toward 0)
//
// Positive => the member is owed money. Negative => the member owes money.
//
// Note: the spec's section 15 prose inverts the payment signs, but its own
// section 9 settlement example (Bob -40 pays Alice +40 => both settled) confirms
// the direction used here.

/**
 * @param {Array<{id:string}>} members
 * @param {Array<{payerMemberId:string, amountMinor:number, shares:Array<{memberId:string, amountMinor:number}>}>} expenses
 * @param {Array<{payerMemberId:string, recipientMemberId:string, amountMinor:number}>} payments
 * @returns {Array<{memberId:string, netMinor:number}>}
 */
export function computeBalances(members, expenses, payments) {
  const net = new Map(members.map((m) => [m.id, 0]));
  const add = (id, delta) => net.set(id, (net.get(id) || 0) + delta);

  for (const e of expenses) {
    add(e.payerMemberId, e.amountMinor);
    for (const s of e.shares) add(s.memberId, -s.amountMinor);
  }
  for (const p of payments) {
    add(p.payerMemberId, p.amountMinor);
    add(p.recipientMemberId, -p.amountMinor);
  }

  return members.map((m) => ({ memberId: m.id, netMinor: net.get(m.id) || 0 }));
}
