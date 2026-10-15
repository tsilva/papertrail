export function money(value: number | null, currency = 'EUR', absolute = false): string {
  if (value === null || !Number.isFinite(value)) return '—';
  try { return new Intl.NumberFormat('en-IE', { style: 'currency', currency }).format(absolute ? Math.abs(value) : value); }
  catch { return `${absolute ? Math.abs(value) : value} ${currency}`; }
}
export function date(value: string): string {
  const parsed = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00Z`) : null;
  return parsed && Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? value : 'Unknown date';
}
export function monthName(value: string): string {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return 'Unknown month';
  const month = new Intl.DateTimeFormat('en-GB', { month: 'long', timeZone: 'UTC' }).format(new Date(`${value}-01T12:00:00Z`));
  return `${value.slice(0, 4)} - ${month}`;
}
export type Document = { id: string; title: string; issuer: string; date: string; type: string; type_key: string; amount: number | null; currency: string; filename: string; matches: string[] };
export type Transaction = { id: string; description: string; date: string; amount: number | null; currency: string; files: string[]; missing_files: string[]; warnings: string[]; reason: string; status: string; revision: string; month: string; statement_id: string; statement_document: string; bank: string; account: string; row_number: number; category: string; method: string; confidence: number | null; match_status: string; file_names: string[] };
export type Statement = {id: string; month: string; document_id: string; bank: string; account: string; period_start: string; period_end: string; filename: string; has_reconciliation: boolean; transaction_count: number};
export type UnmatchedDocument = {id: string; month: string; document_id: string; filename: string; expected: boolean; revision: string};
export function matchLabel(status: string): string { return ({exact: 'Exact match', llm_high: 'AI match', llm_low: 'Low confidence', incomplete: 'Incomplete', unmatched: 'Unmatched', unclassified: 'Unclassified'} as Record<string, string>)[status] || status; }
export type State = { documents: Document[]; transactions: Transaction[]; months: string[]; export_months: string[]; statements: Statement[]; unmatched_documents: UnmatchedDocument[]; errors: string[]; token: string };
