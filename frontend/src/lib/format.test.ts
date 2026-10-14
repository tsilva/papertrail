import { test } from 'node:test';
import assert from 'node:assert/strict';
import { money, date, monthName } from './format.ts';
test('currency comparisons preserve signs, limits and unknown amounts', () => {
  assert.equal(money(-184.5), '-€184.50');
  assert.equal(money(-184.5, 'EUR', true), '€184.50');
  assert.equal(money(null), '—');
  assert.equal(money(0), '€0.00');
});
test('calendar dates do not shift with browser timezone', () => {
  assert.equal(date('2026-10-12'), '12 Oct 2026');
  assert.equal(monthName('2026-10'), 'October 2026');
  assert.equal(date(''), 'Unknown date');
});
