import { test } from 'node:test';
import assert from 'node:assert/strict';

import { computeBalances } from '../js/lib/balances.js';
import { minimizedTransfers, relationshipPreservingTransfers } from '../js/lib/settle.js';

const members = [{ id: 'alice' }, { id: 'bob' }, { id: 'carol' }];
const dinner = {
  payerMemberId: 'alice',
  amountMinor: 9000,
  shares: [
    { memberId: 'alice', amountMinor: 3000 },
    { memberId: 'bob', amountMinor: 3000 },
    { memberId: 'carol', amountMinor: 3000 },
  ],
};
const taxi = {
  payerMemberId: 'bob',
  amountMinor: 3000,
  shares: [
    { memberId: 'bob', amountMinor: 1500 },
    { memberId: 'carol', amountMinor: 1500 },
  ],
};
const carolPaysAlice = { payerMemberId: 'carol', recipientMemberId: 'alice', amountMinor: 1000 };

test('balances are paid minus owed, with payments moving toward zero', () => {
  const balances = computeBalances(members, [dinner, taxi], [carolPaysAlice]);
  assert.deepEqual(balances, [
    { memberId: 'alice', netMinor: 5000 },
    { memberId: 'bob', netMinor: -1500 },
    { memberId: 'carol', netMinor: -3500 },
  ]);
  assert.equal(balances.reduce((sum, b) => sum + b.netMinor, 0), 0);
});

test('minimized transfers settle everyone in at most n - 1 moves', () => {
  const transfers = minimizedTransfers(computeBalances(members, [dinner, taxi], [carolPaysAlice]));
  assert.deepEqual(transfers, [
    { fromMemberId: 'carol', toMemberId: 'alice', amountMinor: 3500 },
    { fromMemberId: 'bob', toMemberId: 'alice', amountMinor: 1500 },
  ]);
});

test('relationship-preserving transfers keep who owes whom', () => {
  const transfers = relationshipPreservingTransfers(members, [dinner, taxi], [carolPaysAlice]);
  assert.deepEqual(transfers, [
    { fromMemberId: 'bob', toMemberId: 'alice', amountMinor: 3000 },
    { fromMemberId: 'carol', toMemberId: 'alice', amountMinor: 2000 },
    { fromMemberId: 'carol', toMemberId: 'bob', amountMinor: 1500 },
  ]);
});

test('a settled group needs no transfers', () => {
  assert.deepEqual(minimizedTransfers(computeBalances(members, [], [])), []);
});
