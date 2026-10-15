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
  assert.equal(date('2026-10-12'), '2026-10-12');
  assert.equal(monthName('2026-10'), '2026 - October');
  assert.equal(date(''), 'Unknown date');
});

test('unknown and invalid dates stay readable and cannot crash month labels', () => {
  for (const value of ['$UNKNOWN$', '2026-02-30', '2026-13-01', 'not a date']) assert.equal(date(value), 'Unknown date');
  for (const value of ['$UNKNOW', '2026-13', '2026-00']) assert.equal(monthName(value), 'Unknown month');
});
import { reviewQueue, selectionAfterDecision } from './review-state.ts';
import type { Transaction } from './format.ts';
const transactions = ['a', 'b', 'c'].map(id => ({ id, month: '2026-10', status: '' } as Transaction));
test('decisions advance to the next pending row and the last row falls back', () => {
  const approved = transactions.map(t => t.id === 'b' ? {...t, status: 'approved'} : t);
  const before = reviewQueue(transactions, '2026-10', 'pending');
  const after = reviewQueue(approved, '2026-10', 'pending');
  assert.equal(selectionAfterDecision(before, after, 'b'), 'c');
  assert.equal(selectionAfterDecision(before, after.filter(t => t.id !== 'c'), 'c'), 'a');
  assert.equal(selectionAfterDecision(before, [], 'a'), '');
  assert.equal(selectionAfterDecision(before, approved, 'b'), 'b');
});
test('review queues respect explicit status and month filters', () => {
  const rows = [{...transactions[0], status: 'approved'}, {...transactions[1], status: 'rejected'}, {...transactions[2], month: '2026-09', status: 'changed'}];
  assert.deepEqual(reviewQueue(rows, '2026-10', 'rejected').map(t => t.id), ['b']);
  assert.deepEqual(reviewQueue(rows, '', 'pending').map(t => t.id), ['c']);
  assert.deepEqual(reviewQueue(rows, '2026-09', 'approved'), []);
});
import { readResponse } from './api.ts';
test('plain-text server failures have readable messages while JSON details survive', async () => {
  await assert.rejects(readResponse(new Response('Internal Server Error', {status: 500}), 'Preview unavailable.'), /Preview unavailable/);
  await assert.rejects(readResponse(Response.json({detail: 'Source file is unavailable.'}, {status: 404}), 'Preview unavailable.'), /Source file is unavailable/);
});

test('monthly attention filters retain unmatched, incomplete and unclassified rows', () => {
  const rows = ['unmatched', 'incomplete', 'unclassified', 'exact'].map((match_status, i) => ({...transactions[0], id: String(i), match_status}));
  for (const filter of ['unmatched', 'incomplete', 'unclassified']) assert.deepEqual(reviewQueue(rows, '', filter).map(t => t.match_status), [filter]);
  assert.equal(reviewQueue(rows, '', 'all').length, 4);
});

import { transactionQueue } from './review-state.ts';
test('monthly transaction search combines bank identity, rule and evidence filters', () => {
  const rows = [{...transactions[0], statement_id:'bank-a', description:'INVOICE', category:'hosting', file_names:['invoice.pdf'], bank:'Bank A', account:'a'}, {...transactions[1], statement_id:'bank-b', description:'REFUND', category:'refund', file_names:[], bank:'Bank B', account:'b'}];
  assert.deepEqual(transactionQueue(rows, '2026-10', 'bank-a', 'all', 'invoice.pdf').map(t => t.id), ['a']);
  assert.deepEqual(transactionQueue(rows, '2026-10', '', 'all', 'refund').map(t => t.id), ['b']);
  assert.deepEqual(transactionQueue(rows, '2026-09', '', 'all', ''), []);
});
