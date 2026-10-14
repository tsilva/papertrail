<script lang="ts">
  import Icon from './lib/Icon.svelte';
  import Preview from './lib/Preview.svelte';
  import { money, date, type Document, type Transaction } from './lib/format';
  let { documents, transactions, month, onreview }: { documents: Document[]; transactions: Transaction[]; month: string; onreview: (id: string) => void } = $props();
  let query = $state(''), type = $state(''), selected = $state('');
  let types = $derived([...new Map(documents.map(d => [d.type_key, d.type])).entries()]);
  let filtered = $derived(documents.filter(d => (!month || d.date.startsWith(month)) && (!type || d.type_key === type) && `${d.title} ${d.issuer} ${d.filename} ${d.type}`.toLowerCase().includes(query.toLowerCase())));
  let doc = $derived(filtered.find(d => d.id === selected) || filtered[0]);
  let matches = $derived(doc ? transactions.filter(t => doc.matches.includes(t.id)) : []);
</script>
<div class="documents-screen">
  <div class="document-filters">
    <label class="search"><Icon name="review" size={22}/><input aria-label="Search documents" placeholder="Search documents…" bind:value={query}/></label>
    <label class="type-filter">Type <select aria-label="Document type" bind:value={type}><option value="">All documents</option>{#each types as [key, title]}<option value={key}>{title}</option>{/each}</select></label>
  </div>
  <div class="documents-layout">
    <section class="collection" aria-label="Documents">
      <table class="document-table"><thead><tr><th>Date</th><th>Document</th><th>Issuer</th><th class="number">Amount</th></tr></thead>
        <tbody>{#each filtered as entry}<tr class:selected={entry.id === doc?.id}>
          <td>{date(entry.date)}</td><td><button class="document-link" onclick={() => selected = entry.id} aria-pressed={doc?.id === entry.id}><Icon name="file" size={28}/><span><strong>{entry.title}</strong><small>{entry.type}</small></span></button></td>
          <td>{entry.issuer}</td><td class="number">{money(entry.amount, entry.currency)}</td>
        </tr>{/each}</tbody>
      </table>
      {#if !filtered.length}<div class="empty"><h2>No documents found</h2><p>{query || type ? 'Try a different search or document type.' : 'Documents will appear after running the pipeline for this profile.'}</p>{#if query || type}<button onclick={() => { query = ''; type = ''; }}>Clear filters</button>{/if}</div>{/if}
    </section>
    <section class="document-detail" aria-label="Selected document">
      {#if doc}
        <div class="detail-heading"><h1>{doc.title}</h1><div class="actions"><a class="button primary" href={`/api/documents/${doc.id}/file`} target="_blank" rel="noreferrer">Open document</a><a class="icon-button" aria-label="Download document" href={`/api/documents/${doc.id}/file?download=true`}><Icon name="download"/></a></div></div>
        {#key doc.id}<Preview document={doc}/>{/key}
        <div class="metadata"><h2>Extracted details</h2><dl><div><dt>Document type</dt><dd>{doc.type}</dd></div><div><dt>Issuer</dt><dd>{doc.issuer}</dd></div><div><dt>Date</dt><dd>{date(doc.date)}</dd></div><div><dt>Amount</dt><dd>{money(doc.amount, doc.currency)}</dd></div></dl></div>
        {#if matches.length}<div class="document-matches">{#each matches as match}<button onclick={() => onreview(match.id)}><Icon name="check"/><span><strong>{match.status === 'approved' ? 'Approved bank match' : match.status === 'rejected' ? 'Rejected bank match' : 'Matched to bank transaction'}</strong><small>{date(match.date)} · {money(match.amount, match.currency)}</small></span><Icon name="arrow" size={18}/></button>{/each}</div>{:else}<p class="unmatched-note">No linked bank transaction</p>{/if}
      {:else}<div class="empty">Select a document to preview.</div>{/if}
    </section>
  </div>
</div>
