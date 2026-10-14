<script lang="ts">
  import { onMount } from 'svelte';
  import Documents from './Documents.svelte';
  import Review from './Review.svelte';
  import Icon from './lib/Icon.svelte';
  import { monthName, type State, type Transaction } from './lib/format';
  import brand from '../papertrail-icon.png';
  let dataset = $state<State>({ documents: [], transactions: [], months: [], errors: [], export_months: [], token: '' });
  let tab = $state(location.hash === '#review' ? 'review' : 'documents');
  let month = $state(''), selected = $state(''), loading = $state(true), busy = $state(false), error = $state(''), notice = $state(''), menu = $state(false);
  let undo = $state<{ id: string; action: 'approve' | 'reject' | 'clear'; revision: string } | null>(null);
  async function read(response: Response): Promise<State> { const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Unable to save this decision.'); return data; }
  async function refresh() {
    loading = true; error = '';
    try { const firstLoad = !dataset.token; dataset = await read(await fetch('/api/state')); if (firstLoad) month = dataset.months[0] || ''; }
    catch (e) { error = e instanceof Error ? e.message : 'Unable to load documents.'; }
    finally { loading = false; }
  }
  async function decide(row: Transaction, action: 'approve' | 'reject' | 'clear', restoring = false) {
    if (busy) return;
    busy = true; error = ''; notice = '';
    try {
      const next = await read(await fetch(`/api/transactions/${row.id}/decision`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-papertrail-token': dataset.token }, body: JSON.stringify({ action, revision: row.revision }) }));
      undo = restoring ? null : { id: row.id, revision: next.transactions.find(t => t.id === row.id)!.revision, action: row.status === 'approved' ? 'approve' : row.status === 'rejected' ? 'reject' : 'clear' };
      dataset = next; notice = restoring ? 'Decision undone.' : action === 'approve' ? 'Match approved.' : action === 'reject' ? 'Match rejected.' : 'Decision cleared.';
      selected = '';
    } catch (e) { error = e instanceof Error ? e.message : 'Unable to save decision.'; }
    finally { busy = false; }
  }
  async function restore() { const row = dataset.transactions.find(t => t.id === undo?.id); if (row && undo) await decide({ ...row, revision: undo.revision }, undo.action, true); }
  function openReview(id: string) { selected = id; month = dataset.transactions.find(t => t.id === id)?.month || month; location.hash = 'review'; tab = 'review'; }
  function keyboard(event: KeyboardEvent) { if (event.target instanceof Element && event.target.closest('input,select,textarea') || event.metaKey || event.ctrlKey) return; if (event.key.toLowerCase() === 'u' && undo) void restore(); }
  onMount(() => { void refresh(); });
</script>
<svelte:window onhashchange={() => tab = location.hash === '#review' ? 'review' : 'documents'} onkeydown={keyboard}/>
<header class="app-header"><a class="brand" href="#documents"><img src={brand} alt=""/><span>paper<span class="teal">trail</span></span></a><nav aria-label="Main navigation"><a href="#documents" class:active={tab === 'documents'} aria-current={tab === 'documents' ? 'page' : undefined}><Icon name="file"/>Documents</a><a href="#review" class:active={tab === 'review'} aria-current={tab === 'review' ? 'page' : undefined}><Icon name="review"/>Review</a></nav><div class="header-actions"><select aria-label="Month" bind:value={month} onchange={() => { selected = ''; notice = ''; }}><option value="">All months</option>{#each dataset.months as value}<option value={value}>{monthName(value)}</option>{/each}</select><div class="menu-wrap"><button class="icon-button bare" aria-label="More options" aria-expanded={menu} onclick={() => menu = !menu}><Icon name="more"/></button>{#if menu}<div class="menu"><button onclick={() => { void refresh(); menu = false; }}>Refresh documents</button>{#if dataset.export_months?.includes(month)}<a href={`/api/export/${month}`}>Export month ZIP</a>{/if}</div>{/if}</div></div></header>
{#if error}<div class="message error" role="alert"><span>{error}</span><button onclick={refresh}>Refresh</button><button aria-label="Dismiss error" onclick={() => error = ''}><Icon name="close" size={16}/></button></div>{/if}
{#if notice}<div class="message success" role="status"><Icon name="check" size={18}/><span>{notice}</span>{#if undo}<button disabled={busy} onclick={restore}>Undo</button>{/if}<button aria-label="Dismiss notice" onclick={() => notice = ''}><Icon name="close" size={16}/></button></div>{/if}
{#each dataset.errors as warning}<div class="message error" role="alert">{warning}</div>{/each}
<main>{#if loading}<div class="empty"><h1>Loading Papertrail…</h1></div>{:else if tab === 'documents'}<Documents documents={dataset.documents} transactions={dataset.transactions} {month} onreview={openReview}/>{:else}<Review documents={dataset.documents} transactions={dataset.transactions} {month} {selected} {busy} onselect={id => selected = id} ondecision={decide}/>{/if}</main>
