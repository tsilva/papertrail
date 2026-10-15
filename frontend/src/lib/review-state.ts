import type { Transaction } from './format';
export function reviewQueue(transactions: Transaction[], month: string, filter: string): Transaction[] {
  return transactions.filter(t => (!month || t.month === month) && (filter === 'all' || (filter === 'pending' ? !['approved', 'rejected'].includes(t.status) : ['unmatched', 'incomplete', 'unclassified'].includes(filter) ? t.match_status === filter : t.status === filter)));
}
export function selectionAfterDecision(before: Transaction[], after: Transaction[], id: string): string {
  if (after.some(t => t.id === id)) return id;
  const index = before.findIndex(t => t.id === id);
  const following = before.slice(index + 1).find(t => after.some(next => next.id === t.id));
  const previous = before.slice(0, index).reverse().find(t => after.some(next => next.id === t.id));
  return following?.id || previous?.id || after[0]?.id || '';
}
export function editing(event: KeyboardEvent): boolean {
  return event.target instanceof Element && !!event.target.closest('input,select,textarea,[contenteditable]:not([contenteditable="false"])');
}
export function transactionQueue(transactions: Transaction[], month: string, bank: string, filter: string, query: string): Transaction[] {
  return reviewQueue(transactions, month, filter).filter(t => (!bank || t.statement_id === bank) && `${t.description} ${t.bank} ${t.account} ${t.category} ${(t.file_names || []).join(' ')}`.toLowerCase().includes(query.toLowerCase()));
}
