// Resolves a split method + raw inputs into an exact allocation in minor units.
//
// Deterministic rounding: allocate the floor of each weighted portion, then hand
// out the leftover minor units one at a time, ordered by largest fractional part
// and then by member id ascending. The allocation always reconciles exactly to
// the expense total.

/**
 * @param {object} args
 * @param {'equal'|'percentage'|'share'|'exact'} args.splitType
 * @param {number} args.amountMinor  expense total, integer minor units
 * @param {Array<{memberId:string, raw?:string|number}>} args.participants
 *        For 'exact', raw is already minor units. For 'percentage' raw is a
 *        percent (0-100), for 'share' raw is a positive weight.
 * @returns {{ok:boolean, shares:Array<{memberId:string, amountMinor:number}>,
 *            allocatedMinor:number, error:string|null}}
 */
export function computeAllocation({ splitType, amountMinor, participants }) {
  const empty = { ok: false, shares: [], allocatedMinor: 0, error: null };

  if (!participants || participants.length === 0) {
    return { ...empty, error: 'Select at least one participant' };
  }
  if (!(amountMinor > 0)) {
    return { ...empty, error: 'Enter an amount greater than zero' };
  }

  const ids = participants.map((p) => p.memberId);
  let shares;
  let error = null;

  switch (splitType) {
    case 'equal': {
      shares = allocateByWeights(amountMinor, ids.map(() => 1), ids);
      break;
    }
    case 'share': {
      const weights = participants.map((p) => Number(p.raw));
      if (weights.some((w) => !(w > 0))) {
        return { ...empty, error: 'Every share must be a positive number' };
      }
      shares = allocateByWeights(amountMinor, weights, ids);
      break;
    }
    case 'percentage': {
      const pcts = participants.map((p) => Number(p.raw));
      if (pcts.some((p) => Number.isNaN(p) || p < 0)) {
        return { ...empty, error: 'Every percentage must be zero or more' };
      }
      // Work in basis points (hundredths of a percent) to stay integer.
      const bp = pcts.map((p) => Math.round(p * 100));
      const totalBp = bp.reduce((a, b) => a + b, 0);
      shares = totalBp === 0
        ? ids.map((memberId) => ({ memberId, amountMinor: 0 }))
        : allocateByWeights(amountMinor, bp, ids);
      if (totalBp !== 10000) {
        error = `Percentages total ${(totalBp / 100).toFixed(2)}% — they must total 100%`;
      }
      break;
    }
    case 'exact': {
      const vals = participants.map((p) => Math.round(Number(p.raw)));
      if (vals.some((v) => Number.isNaN(v) || v < 0)) {
        return { ...empty, error: 'Every amount must be zero or more' };
      }
      shares = ids.map((memberId, i) => ({ memberId, amountMinor: vals[i] }));
      break;
    }
    default:
      return { ...empty, error: `Unknown split type: ${splitType}` };
  }

  const allocatedMinor = shares.reduce((a, s) => a + s.amountMinor, 0);
  if (!error && allocatedMinor !== amountMinor) {
    error = 'Allocation does not reconcile to the expense total';
  }
  return { ok: error === null, shares, allocatedMinor, error };
}

function allocateByWeights(amountMinor, weights, ids) {
  const total = weights.reduce((a, b) => a + b, 0);
  if (!(total > 0)) return ids.map((memberId) => ({ memberId, amountMinor: 0 }));

  const exact = weights.map((w) => (amountMinor * w) / total);
  const floors = exact.map(Math.floor);
  const remainder = Math.round(amountMinor - floors.reduce((a, b) => a + b, 0));

  const order = exact
    .map((v, i) => ({ i, frac: v - Math.floor(v), id: String(ids[i]) }))
    .sort((a, b) => b.frac - a.frac || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  const out = floors.slice();
  for (let k = 0; k < remainder; k++) out[order[k].i] += 1;
  return ids.map((memberId, i) => ({ memberId, amountMinor: out[i] }));
}
