import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseMoneyToMinor, minorToInput, currencyInfo } from '../js/lib/money.js';

test('parses typed amounts into integer minor units', () => {
  assert.deepEqual(parseMoneyToMinor('12.34', 'USD'), { ok: true, minor: 1234 });
  assert.deepEqual(parseMoneyToMinor('1,000.5', 'EUR'), { ok: true, minor: 100050 });
  assert.deepEqual(parseMoneyToMinor('.5', 'USD'), { ok: true, minor: 50 });
  assert.deepEqual(parseMoneyToMinor('-3', 'USD'), { ok: true, minor: -300 });
  assert.deepEqual(parseMoneyToMinor('500', 'JPY'), { ok: true, minor: 500 });
});

test('rejects what is not an amount in that currency', () => {
  for (const input of ['', '  ', '.', '-', 'abc', '1.2.3']) {
    assert.equal(parseMoneyToMinor(input, 'USD').ok, false, input);
  }
  assert.equal(parseMoneyToMinor('1.234', 'USD').ok, false);
  assert.equal(parseMoneyToMinor('1.5', 'JPY').ok, false);
});

test('minorToInput round-trips through parseMoneyToMinor', () => {
  for (const [minor, code] of [[0, 'USD'], [5, 'USD'], [1234, 'EUR'], [-99, 'GBP'], [700, 'JPY']]) {
    assert.deepEqual(parseMoneyToMinor(minorToInput(minor, code), code), { ok: true, minor });
  }
});

test('an unknown currency falls back to the first one', () => {
  assert.equal(currencyInfo('XXX').code, 'USD');
});
