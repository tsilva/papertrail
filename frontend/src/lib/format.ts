export function money(value: number | null, currency = 'EUR', absolute = false): string {
  if (value === null || !Number.isFinite(value)) return '—';
  try { return new Intl.NumberFormat('en-IE', { style: 'currency', currency }).format(absolute ? Math.abs(value) : value); }
  catch { return `${absolute ? Math.abs(value) : value} ${currency}`; }
}
export function date(value: string): string {
  const parsed = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00Z`) : null;
  return parsed && Number.isFinite(parsed.getTime()) ? new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(parsed) : value || 'Unknown date';
}
export function monthName(value: string): string {
  if (!/^\d{4}-\d{2}$/.test(value)) return value;
  return new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}-01T12:00:00Z`));
}
export type Document = { id: string; title: string; issuer: string; date: string; type: string; type_key: string; amount: number | null; currency: string; filename: string; matches: string[] };
export type Transaction = { id: string; description: string; date: string; amount: number | null; currency: string; files: string[]; missing_files: string[]; warnings: string[]; reason: string; status: string; revision: string; month: string };
export type State = { documents: Document[]; transactions: Transaction[]; months: string[]; export_months: string[]; errors: string[]; token: string };
