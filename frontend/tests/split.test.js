import { test } from 'node:test';
import assert from 'node:assert/strict';

import { computeAllocation } from '../js/lib/split.js';

const people = (...ids) => ids.map((memberId) => ({ memberId }));
const amounts = (result) => Object.fromEntries(result.shares.map((s) => [s.memberId, s.amountMinor]));

test('an equal split hands leftover cents out by member id', () => {
  const result = computeAllocation({ splitType: 'equal', amountMinor: 1000, participants: people('c', 'a', 'b') });
  assert.equal(result.ok, true);
  assert.deepEqual(amounts(result), { a: 334, b: 333, c: 333 });
});

test('shares are weighted and reconcile to the total', () => {
  const result = computeAllocation({
    splitType: 'share',
    amountMinor: 1001,
    participants: [{ memberId: 'a', raw: 2 }, { memberId: 'b', raw: 1 }, { memberId: 'c', raw: 1 }],
  });
  assert.equal(result.ok, true);
  assert.equal(result.allocatedMinor, 1001);
  assert.deepEqual(amounts(result), { a: 501, b: 250, c: 250 });
});

test('percentages must total 100', () => {
  const ok = computeAllocation({
    splitType: 'percentage',
    amountMinor: 1000,
    participants: [{ memberId: 'a', raw: 70 }, { memberId: 'b', raw: 30 }],
  });
  assert.deepEqual(amounts(ok), { a: 700, b: 300 });

  const short = computeAllocation({
    splitType: 'percentage',
    amountMinor: 1000,
    participants: [{ memberId: 'a', raw: 50 }, { memberId: 'b', raw: 20 }],
  });
  assert.equal(short.ok, false);
  assert.match(short.error, /must total 100%/);
});

test('exact amounts must add up to the total', () => {
  const result = computeAllocation({
    splitType: 'exact',
    amountMinor: 1000,
    participants: [{ memberId: 'a', raw: 600 }, { memberId: 'b', raw: 300 }],
  });
  assert.equal(result.ok, false);
  assert.equal(result.allocatedMinor, 900);
});

test('needs participants and a positive amount', () => {
  assert.equal(computeAllocation({ splitType: 'equal', amountMinor: 100, participants: [] }).ok, false);
  assert.equal(computeAllocation({ splitType: 'equal', amountMinor: 0, participants: people('a') }).ok, false);
  assert.equal(computeAllocation({ splitType: 'share', amountMinor: 100, participants: [{ memberId: 'a', raw: 0 }] }).ok, false);
});
