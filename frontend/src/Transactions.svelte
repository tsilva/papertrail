<script lang="ts">
  import { onMount } from 'svelte';
  import Icon from './lib/Icon.svelte';
  import Preview from './lib/Preview.svelte';
  import { money, date, matchLabel, type Document, type Transaction, type Statement, type UnmatchedDocument } from './lib/format';
  import { transactionQueue } from './lib/review-state';
  let {documents, transactions, statements, unmatched, month, exportable, selected, busy, onselect, onreview, ondecision, onexpected, query = $bindable(''), bank = $bindable(''), filter = $bindable('all'), file = $bindable('')}: {
    documents: Document[]; transactions: Transaction[]; statements: Statement[]; unmatched: UnmatchedDocument[]; month: string; exportable: boolean; selected: string; busy: boolean;
    onselect: (id: string) => void; onreview: (id: string) => void; ondecision: (row: Transaction, action: 'approve' | 'reject' | 'clear') => void; onexpected: (item: UnmatchedDocument) => void;
    query: string; bank: string; filter: string; file: string;
  } = $props();
  let monthStatements = $derived(statements.filter(s => !month || s.month === month));
  let visibleStatements = $derived(monthStatements.filter(s => !bank || s.id === bank));
  let monthRows = $derived(transactions.filter(t => (!month || t.month === month) && (!bank || t.statement_id === bank)));
  let pending = $derived(monthRows.filter(t => !['approved', 'rejected'].includes(t.status)).length);
  let rows = $derived(transactionQueue(monthRows, '', '', filter, query));
  let row = $derived(rows.find(t => t.id === selected) || rows[0]);
  let sources = $derived(row ? documents.filter(d => row.files.includes(d.id) || d.id === row.statement_document) : []);
  let doc = $derived(documents.find(d => d.id === file) || sources.find(d => d.id === row?.files[0]) || sources[0]);
  let evidenceRow = $derived(row && (!file || row.files.includes(file) || row.statement_document === file) ? row : undefined);
  let orphanRows = $derived(unmatched.filter(d => !month || d.month === month));
  let evidence: HTMLElement;
  onMount(() => {
    // Sticky evidence fills the visible space below the current header/filter area.
    const fitEvidence = () => {
      const height = `${Math.max(320, window.innerHeight - Math.max(0, evidence.getBoundingClientRect().top))}px`;
      if (evidence.style.getPropertyValue('--evidence-height') !== height) evidence.style.setProperty('--evidence-height', height);
    };
    const observer = new ResizeObserver(fitEvidence);
    observer.observe(document.body);
    window.addEventListener('resize', fitEvidence);
    window.addEventListener('scroll', fitEvidence, {passive: true});
    fitEvidence();
    return () => { observer.disconnect(); window.removeEventListener('resize', fitEvidence); window.removeEventListener('scroll', fitEvidence); };
  });
  function select(item: Transaction, document = '') { onselect(item.id); file = document; }
</script>
{#snippet tableHead(label: string)}
  <colgroup><col style="width:16%"/><col style="width:40%"/><col style="width:14%"/><col style="width:14%"/><col style="width:16%"/></colgroup>
  <thead><tr><th>Date</th><th>{label}</th><th class="number">Amount</th><th>Documents</th><th>Status</th></tr></thead>
{/snippet}
<div class="transactions-screen">
  <div class="transaction-heading"><div><h1>Transactions</h1><p>{monthRows.length} transactions{#if monthRows.length} · {pending ? `${pending} to review` : 'All transactions reviewed'}{/if}</p></div>{#if exportable}<a class="button" href={`/api/export/${month}`}><Icon name="download" size={18}/>Export month ZIP</a>{/if}</div>
  <div class="transaction-filters">
    <label class="search"><Icon name="review"/><input aria-label="Search transactions" placeholder="Search transactions…" bind:value={query}/></label>
    <select aria-label="Bank account" bind:value={bank} onchange={() => {onselect(''); file = '';}}><option value="">All banks</option>{#each monthStatements as statement}<option value={statement.id}>{statement.bank}{statement.account ? ` · ${statement.account}` : ''}{#if !month || monthStatements.filter(s => s.bank === statement.bank && s.account === statement.account).length > 1} · {date(statement.period_start)}{/if}</option>{/each}</select>
    <select aria-label="Transaction status" bind:value={filter} onchange={() => {onselect(''); file = '';}}><option value="all">All statuses</option><option value="pending">Needs review</option><option value="unmatched">Unmatched</option><option value="incomplete">Incomplete</option><option value="unclassified">Unclassified</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select>
  </div>
  <div class="transaction-layout">
    <section class="transaction-collection" aria-label="Monthly transactions">
      {#each visibleStatements as statement}
        {@const bankRows = rows.filter(t => t.statement_id === statement.id)}
        <section class="statement-section">
          <div class="statement-heading"><div><h2>{statement.bank}{#if statement.account}<span> · {statement.account}</span>{/if}</h2><p>{date(statement.period_start)} – {date(statement.period_end)} · {statement.transaction_count} {statement.transaction_count === 1 ? 'transaction' : 'transactions'}</p></div>{#if statement.document_id}<button class="text-button" onclick={() => file = statement.document_id}>View statement</button>{/if}</div>

          {#if !statement.has_reconciliation}<div class="statement-empty" role="status">Reconciliation has not run for this statement. Run reconciliation to load its transaction matches.</div>{:else if !bankRows.length}<p class="statement-empty">No transactions match these filters.</p>{:else}
          <div class="transaction-table-scroll"><table class="transaction-table">{@render tableHead('Transaction')}<tbody>
            {#each bankRows as item}<tr class:selected={item.id === evidenceRow?.id}>
              <td>{date(item.date)}</td>
              <td><button class="transaction-link" aria-pressed={item.id === evidenceRow?.id} onclick={() => select(item)}><strong>{item.description}</strong></button></td>
              <td class="number">{item.amount !== null && item.amount > 0 ? '+' : ''}{money(item.amount, item.currency)}</td>
              <td class="transaction-files">{#if item.files.length}<button class="text-button" aria-label={`${item.files.length} matched ${item.files.length === 1 ? 'document' : 'documents'}`} title={item.files.map(id => documents.find(d => d.id === id)?.title || 'Document').join(' · ')} onclick={() => select(item, item.files[0])}>{item.files.length}</button>{:else}<span class="muted">0</span>{/if}{#each item.missing_files as name}<small class="warning">Missing: {name}</small>{/each}</td>
              <td class="transaction-actions">
                {#if item.status === 'approved'}<button class="text-button approved" disabled={busy} onclick={() => ondecision(item, 'clear')}>Approved</button>
                {:else}{#if item.missing_files.length}<span class="match-status attention">Missing evidence</span>{:else if !['exact', 'llm_high'].includes(item.match_status)}<span class="match-status attention">{matchLabel(item.match_status)}</span>{/if}{#if item.files.length && !item.missing_files.length}<button class="text-button" disabled={busy} onclick={() => ondecision(item,'approve')}>{item.status === 'changed' ? 'Changed · approve' : item.status === 'rejected' ? 'Rejected · approve' : 'Approve'}</button>{/if}{/if}
                {#if item.status === 'approved' && ['incomplete','unclassified','llm_low'].includes(item.match_status)}<small class="warning">{matchLabel(item.match_status)}</small>{/if}
              </td>
            </tr>{/each}
          </tbody></table></div>{/if}
        </section>
      {/each}
      {#if !visibleStatements.length}<div class="empty"><h2>No bank statements for this month</h2><p>Download the statements and run the monthly pipeline. Documents are still available in the Documents tab.</p></div>{/if}
      {#if orphanRows.length}
        <section class="statement-section orphan-section" aria-label="Documents without a transaction">
          <div class="statement-heading"><div><h2>Documents without a transaction</h2><p>{orphanRows.filter(d => !d.expected).length} pending · {orphanRows.filter(d => d.expected).length} expected</p></div></div>
          <div class="transaction-table-scroll"><table class="transaction-table">{@render tableHead('Document')}<tbody>
            {#each orphanRows as item}
              {@const document = documents.find(d => d.id === item.document_id)}
              <tr class:selected={!!document && doc?.id === item.document_id}>
                <td>{date(document?.date || '')}</td>
                <td><button class="transaction-link" disabled={!document} aria-pressed={!!document && doc?.id === item.document_id} onclick={() => file = item.document_id}><strong>{document?.title || item.filename}</strong></button></td>
                <td class="number">{money(document?.amount ?? null, document?.currency)}</td>
                <td class="transaction-files">{#if document}<button class="text-button" aria-label="1 document" onclick={() => file = item.document_id}>1</button>{:else}<span class="muted">0</span>{/if}</td>
                <td class="transaction-actions"><button disabled={busy || !item.document_id} class="text-button" onclick={() => onexpected(item)}>{item.expected ? 'Expected · undo' : 'Mark expected'}</button></td>
              </tr>
            {/each}
          </tbody></table></div>
        </section>
      {/if}
    </section>
    <aside class="transaction-evidence" aria-label="Transaction evidence" bind:this={evidence}>
      {#if doc}<div class="evidence-heading compact-evidence-heading">{#if sources.length > 1 && evidenceRow}<select aria-label="Evidence document" value={doc.id} onchange={event => file = event.currentTarget.value}>{#each sources as source}<option value={source.id}>{source.id === row?.statement_document ? 'Bank statement' : source.title}</option>{/each}</select>{:else}<h2>{doc.title}</h2>{/if}{#if evidenceRow && row}<button class="text-button review-link" onclick={() => onreview(row.id)}>Review<Icon name="arrow" size={16}/></button>{/if}</div>
        {#if evidenceRow && row}{#each row.warnings as warning}<p class="evidence-warning">{warning}</p>{/each}{/if}
        {#key doc.id}<Preview document={doc} showTitle={false}/>{/key}
      {:else}<div class="empty"><h2>No linked document</h2><p>Select a transaction or a statement to inspect its evidence.</p>{#if row}<button onclick={() => onreview(row.id)}>Review transaction</button>{/if}</div>{/if}
    </aside>
  </div>
</div>
