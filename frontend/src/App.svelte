<script lang="ts">
  import { onMount } from 'svelte';
  import Transactions from './Transactions.svelte';
  import Documents from './Documents.svelte';
  import Review from './Review.svelte';
  import Icon from './lib/Icon.svelte';
  import { monthName, type State, type Transaction, type UnmatchedDocument } from './lib/format';
  import { editing, reviewQueue, selectionAfterDecision, transactionQueue } from './lib/review-state';
  import { readResponse } from './lib/api';
  import brand from '../papertrail-icon.png';
  let dataset = $state<State>({ documents: [], transactions: [], months: [], errors: [], export_months: [], statements: [], unmatched_documents: [], token: '' });
  let tab = $state(location.hash === '#documents' ? 'documents' : location.hash === '#review' ? 'review' : 'transactions');
  let month = $state(''), selected = $state(''), loading = $state(true), busy = $state(false), error = $state(''), notice = $state('');
  let documentQuery = $state(''), documentType = $state(''), documentSelected = $state('');
  let transactionQuery = $state(''), transactionBank = $state(''), transactionFilter = $state('all'), transactionFile = $state('');
  let reviewFilter = $state('pending'), reviewFile = $state('');
  let undo = $state<{ id: string; action: 'approve' | 'reject' | 'clear'; revision: string } | null>(null);
  async function read(response: Response): Promise<State> { return readResponse<State>(response, 'Unable to load dashboard data. Try refreshing.'); }
  async function refresh() {
    loading = true; error = '';
    try { const firstLoad = !dataset.token; dataset = await read(await fetch('/api/state')); if (firstLoad) month = dataset.export_months[0] || dataset.months[0] || ''; }
    catch (e) { error = e instanceof Error ? e.message : 'Unable to load documents.'; }
    finally { loading = false; }
  }
  async function decide(row: Transaction, action: 'approve' | 'reject' | 'clear', restoring = false) {
    if (busy) return;
    busy = true; error = ''; notice = '';
    try {
      const next = await read(await fetch(`/api/transactions/${row.id}/decision`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-papertrail-token': dataset.token }, body: JSON.stringify({ action, revision: row.revision }) }));
      undo = restoring ? null : { id: row.id, revision: next.transactions.find(t => t.id === row.id)!.revision, action: row.status === 'approved' ? 'approve' : row.status === 'rejected' ? 'reject' : 'clear' };
      if (restoring && month && month !== row.month) month = row.month;
      const visible = (rows: Transaction[]) => tab === 'transactions' ? transactionQueue(rows, month, transactionBank, transactionFilter, transactionQuery) : reviewQueue(rows, month, reviewFilter);
      selected = restoring ? row.id : selectionAfterDecision(visible(dataset.transactions), visible(next.transactions), row.id);
      if (restoring && !transactionQueue(next.transactions, month, transactionBank, transactionFilter, transactionQuery).some(t => t.id === row.id)) { transactionBank = ''; transactionFilter = 'all'; transactionQuery = ''; }
      transactionFile = '';
      if (restoring && !reviewQueue(next.transactions, month, reviewFilter).some(t => t.id === row.id)) reviewFilter = 'all';
      reviewFile = '';
      dataset = next; notice = restoring ? 'Decision undone.' : action === 'approve' ? 'Match approved.' : action === 'reject' ? 'Match rejected.' : 'Decision cleared.';
    } catch (e) { error = e instanceof Error ? e.message : 'Unable to save decision.'; }
    finally { busy = false; }
  }
  async function expectDocument(item: UnmatchedDocument) {
    if (busy) return;
    busy = true; error = '';
    try {
      dataset = await read(await fetch(`/api/unmatched/${item.id}/decision`, {method: 'POST', headers: {'Content-Type':'application/json','x-papertrail-token':dataset.token}, body: JSON.stringify({expected: !item.expected, revision:item.revision})}));
      undo = null; notice = item.expected ? 'Expected status removed.' : 'Document marked as expected without a transaction.';
    } catch(e) { error = e instanceof Error ? e.message : 'Unable to save decision.'; }
    finally {busy = false;}
  }
  async function restore() { const row = dataset.transactions.find(t => t.id === undo?.id); if (row && undo) await decide({ ...row, revision: undo.revision }, undo.action, true); }
  function openReview(id: string) { reviewFilter = 'all'; reviewFile = ''; selected = id; month = dataset.transactions.find(t => t.id === id)?.month || month; location.hash = 'review'; tab = 'review'; }
  function keyboard(event: KeyboardEvent) {
    if (editing(event) || event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
    if (event.key.toLowerCase() === 'u' && undo) { event.preventDefault(); void restore(); }
  }
  onMount(() => { void refresh(); });
</script>
<svelte:window onhashchange={() => tab = location.hash === '#documents' ? 'documents' : location.hash === '#review' ? 'review' : 'transactions'} onkeydown={keyboard}/>
<header class="app-header"><a class="brand" href="#transactions"><img src={brand} alt=""/><span>paper<span class="teal">trail</span></span></a><nav aria-label="Main navigation"><a href="#transactions" class:active={tab !== 'documents'} aria-current={tab !== 'documents' ? 'page' : undefined}><Icon name="review"/>Transactions</a><a href="#documents" class:active={tab === 'documents'} aria-current={tab === 'documents' ? 'page' : undefined}><Icon name="file"/>Documents</a></nav><div class="header-actions"><select aria-label="Month" bind:value={month} onchange={() => { selected = ''; reviewFile = ''; transactionFile = ''; transactionBank = ''; notice = ''; }}><option value="">All months</option>{#each dataset.months as value}<option value={value}>{monthName(value)}</option>{/each}</select><button class="icon-button bare" aria-label="Refresh data" disabled={loading || busy} onclick={refresh}><Icon name="refresh"/></button></div></header>
{#if error}<div class="message error" role="alert"><span>{error}</span><button onclick={refresh}>Refresh</button><button aria-label="Dismiss error" onclick={() => error = ''}><Icon name="close" size={16}/></button></div>{/if}
{#if notice}<div class="message success" role="status"><Icon name="check" size={18}/><span>{notice}</span>{#if undo}<button disabled={busy} onclick={restore}>Undo</button>{/if}<button aria-label="Dismiss notice" onclick={() => notice = ''}><Icon name="close" size={16}/></button></div>{/if}
{#each dataset.errors as warning}<div class="message error" role="alert">{warning}</div>{/each}
<main>{#if loading}<div class="empty"><h1>Loading Papertrail…</h1></div>{:else if tab === 'documents'}<Documents documents={dataset.documents} transactions={dataset.transactions} {month} bind:query={documentQuery} bind:type={documentType} bind:selected={documentSelected} onreview={openReview}/>{:else if tab === 'transactions'}<Transactions documents={dataset.documents} transactions={dataset.transactions} statements={dataset.statements} unmatched={dataset.unmatched_documents} {month} exportable={dataset.export_months.includes(month)} {selected} {busy} onselect={id => selected = id} onreview={openReview} ondecision={decide} onexpected={expectDocument} bind:query={transactionQuery} bind:bank={transactionBank} bind:filter={transactionFilter} bind:file={transactionFile}/>{:else}<div class="review-back"><a href="#transactions"><Icon name="left" size={16}/>Back to monthly transactions</a></div><Review documents={dataset.documents} transactions={dataset.transactions} {month} {selected} {busy} bind:filter={reviewFilter} bind:file={reviewFile} onselect={id => { selected = id; reviewFile = ''; }} ondecision={decide}/>{/if}</main>
