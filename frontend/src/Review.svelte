<script lang="ts">
  import Icon from './lib/Icon.svelte';
  import Preview from './lib/Preview.svelte';
  import { money, date, type Document, type Transaction } from './lib/format';
  import { editing, reviewQueue } from './lib/review-state';
  let { documents, transactions, month, selected, onselect, ondecision, busy, filter = $bindable('pending'), file = $bindable('') }: { documents: Document[]; transactions: Transaction[]; month: string; selected: string; onselect: (id: string) => void; ondecision: (row: Transaction, action: 'approve' | 'reject' | 'clear') => void; busy: boolean; filter: string; file: string } = $props();
  let menu = $state(false);
  let menuElement = $state<HTMLDivElement>();
  let queue = $derived(reviewQueue(transactions, month, filter));
  let row = $derived(queue.find(t => t.id === selected) || queue[0]);
  let sources = $derived(row ? documents.filter(d => row.files.includes(d.id)) : []);
  let doc = $derived(sources.find(d => d.id === file) || sources[0]);
  let index = $derived(row ? queue.findIndex(t => t.id === row.id) : -1);
  let total = $derived(sources.every(d => d.amount !== null && d.currency === row?.currency) && sources.length ? sources.reduce((sum, d) => sum + (d.amount || 0), 0) : null);
  let amountMatches = $derived(total !== null && row?.amount !== null && Math.abs(Math.abs(total) - Math.abs(row?.amount ?? 0)) < .005);
  let dateMatches = $derived(sources.length === 1 && !!doc?.date && doc?.date === row?.date);
  let canReview = $derived(!!sources.length && !row?.missing_files.length);
  function move(direction: number) { const next = queue[index + direction]; if (next) onselect(next.id); }
  function keyboard(event: KeyboardEvent) {
    if (event.key === 'Escape') { menu = false; return; }
    if (editing(event) || event.ctrlKey || event.metaKey || event.altKey || busy) return;
    if (['ArrowDown', 'j'].includes(event.key)) { event.preventDefault(); move(1); }
    if (['ArrowUp', 'k'].includes(event.key)) { event.preventDefault(); move(-1); }
    if (event.repeat) return;
    if (row && canReview && event.key.toLowerCase() === 'y' && row.status !== 'approved') { event.preventDefault(); ondecision(row, 'approve'); }
    if (row && canReview && event.key.toLowerCase() === 'n' && row.status !== 'rejected') { event.preventDefault(); ondecision(row, 'reject'); }
  }
  function dismissMenu(event: MouseEvent) { if (event.target instanceof Node && !menuElement?.contains(event.target)) menu = false; }
</script>
<svelte:window onkeydown={keyboard} onclick={dismissMenu}/>
<div class="review-layout">
  <aside class="queue"><div class="queue-heading"><h1>{filter === 'pending' ? 'To review' : filter === 'all' ? 'Transactions' : filter === 'approved' ? 'Approved' : 'Rejected'} · {queue.length}</h1><select aria-label="Review status" bind:value={filter} onchange={() => { onselect(''); file = ''; menu = false; }}><option value="pending">Needs review</option><option value="all">All statuses</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select></div>
    <div class="queue-list">{#each queue as item}<button class="queue-item" class:selected={item.id === row?.id} onclick={() => { onselect(item.id); menu = false; }}><strong>{item.description}</strong><small>{item.bank}{item.account ? ` · ${item.account}` : ''}</small><span>{item.amount !== null && item.amount > 0 ? '+' : ''}{money(item.amount, item.currency)}</span><small>{date(item.date)}</small>{#if !item.files.length}<small class="warning">Missing document</small>{:else if item.status === 'changed'}<small class="warning">Match changed</small>{/if}</button>{/each}</div>
  </aside>
  <section class="review-source" aria-label="Match evidence">{#if doc}{#if sources.length > 1}<div class="source-picker"><label>Matched document <select aria-label="Matched document" bind:value={file}>{#each sources as source}<option value={source.id}>{source.title}</option>{/each}</select></label><small>{sources.length} documents</small></div>{/if}{#key doc.id}<Preview document={doc} showTitle={sources.length < 2}/>{/key}{:else}<div class="empty"><Icon name="file" size={38}/><h2>{row ? 'No matching document' : 'Nothing to review'}</h2><p>{row ? 'Run reconciliation after adding the missing document.' : 'Choose another month or review status.'}</p></div>{/if}</section>
  <aside class="inspector" aria-label="Review decision">{#if row}
    <p class="bank-context">{row.bank}{row.account ? ` · ${row.account}` : ''}</p><h1>{row.description}</h1>
<section class="comparison-section"><h2>Document comparison</h2><table class="comparison"><thead><tr><th></th><th>{sources.length > 1 ? 'Documents' : 'Document'}</th><th>Bank</th><th></th></tr></thead><tbody><tr><th>Amount</th><td>{money(total, row.currency, true)}</td><td>{row.amount !== null && row.amount > 0 ? '+' : ''}{money(row.amount, row.currency)}</td><td>{#if amountMatches}<Icon name="check" size={18}/>{/if}</td></tr><tr><th>Date</th><td>{sources.length === 1 ? date(doc?.date || '') : sources.length > 1 ? 'Multiple dates' : '—'}</td><td>{date(row.date)}</td><td>{#if dateMatches}<Icon name="check" size={18}/>{/if}</td></tr></tbody></table></section>
    <details class="match-details"><summary>Match details</summary><dl class="transaction-info"><dt>Row</dt><dd>{row.row_number}</dd><dt>Rule</dt><dd>{row.category || 'No rule'}</dd><dt>Method</dt><dd>{row.method || 'Unmatched'}</dd><dt>Confidence</dt><dd>{row.confidence ? `${Math.round(row.confidence * 100)}%` : '—'}</dd></dl>{#if row.reason}<p class="match-reason">{row.reason}</p>{/if}</details>
    {#if row.warnings.length || row.status === 'changed' || row.missing_files.length}<div class="review-warning" role="status">{#if row.status === 'changed'}The evidence changed since the previous decision. {/if}{#each row.warnings as warning}<p>{warning}</p>{/each}{#if row.missing_files.length}<p>Some matched documents are unavailable.</p>{/if}</div>{/if}
    <section class="decision"><h2>{row.status === 'approved' ? 'Approved match' : row.status === 'rejected' ? 'Rejected match' : !sources.length ? 'Missing document' : 'Suggested match'}</h2><div class="actions"><button class="primary" disabled={busy || !canReview || row.status === 'approved'} onclick={() => ondecision(row, 'approve')}><Icon name="check" size={21}/>Approve match</button><button disabled={busy || !canReview || row.status === 'rejected'} onclick={() => ondecision(row, 'reject')}><Icon name="close" size={21}/>Reject</button><div class="menu-wrap" bind:this={menuElement}><button class="icon-button" aria-label="Review actions" aria-expanded={menu} onclick={() => menu = !menu}><Icon name="more"/></button>{#if menu}<div class="menu"><button disabled={busy || !row.status} onclick={() => { ondecision(row, 'clear'); menu = false; }}>Clear decision</button><small>Y approve · N reject<br/>↑ ↓ navigate</small></div>{/if}</div></div></section>
    <div class="row-navigation"><span>{index + 1} of {queue.length}</span><div class="actions"><button class="icon-button" aria-label="Previous transaction" disabled={index <= 0 || busy} onclick={() => move(-1)}><Icon name="left"/></button><button class="icon-button" aria-label="Next transaction" disabled={index >= queue.length - 1 || busy} onclick={() => move(1)}><Icon name="right"/></button></div></div>
  {:else}<div class="empty"><h2>{filter === 'pending' ? 'Review complete' : 'No transactions found'}</h2><p>{filter === 'pending' ? 'No transactions need review in this view.' : 'Choose another month or review status.'}</p></div>{/if}</aside>
</div>
