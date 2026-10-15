<script lang="ts">
  import { onMount } from 'svelte';
  import Icon from './Icon.svelte';
  import { readResponse } from './api';
  import type { Document } from './format';
  let { document, showTitle = true }: { document: Document; showTitle?: boolean } = $props();
  let page = $state(0), pages = $state(1), image = $state(''), rows = $state<string[][]>([]);
  let loading = $state(true), error = $state(''), zoom = $state(false);
  let menu = $state(false);
  let menuElement = $state<HTMLDivElement>();
  let menuButton = $state<HTMLButtonElement>();
  function keyboard(event: KeyboardEvent) { if (event.key === 'Escape' && menu) { menu = false; menuButton?.focus(); } }
  function dismissMenu(event: MouseEvent) { if (event.target instanceof Node && !menuElement?.contains(event.target)) menu = false; }
  let raw = $state(false), metadata = $state(''), metadataError = $state('');
  async function toggleMetadata() {
    raw = !raw;
    if (!raw || metadata) return;
    metadataError = '';
    try { metadata = JSON.stringify(await readResponse(await fetch(`/api/documents/${document.id}/metadata`), 'Could not load document metadata.'), null, 2); }
    catch(e) { metadataError = e instanceof Error ? e.message : 'Metadata unavailable.'; }
  }
  let frame: HTMLDivElement;
  let request = 0;
  async function load() {
    const current = ++request;
    loading = true; error = '';
    try {
      const response = await fetch(`/api/documents/${document.id}/preview?page=${page}`);
      const data = await readResponse<{pages: number; image?: string; rows?: string[][]}>(response, 'Could not load this document. Try again or refresh the dashboard.');
      if (current !== request) return;
      pages = data.pages; image = data.image || ''; rows = data.rows || [];
    } catch (e) { if (current === request) error = e instanceof Error ? e.message : 'Preview unavailable.'; }
    finally { if (current === request) loading = false; }
  }
  $effect(() => { document.id; page; void load(); });
  onMount(() => () => { request++; });
</script>
<svelte:window onkeydown={keyboard} onclick={dismissMenu}/>
<div class="source" bind:this={frame} class:zoom>
  <div class="source-tools" class:multiple={pages > 1} class:compact={!showTitle}>
    {#if showTitle || (pages > 1 && !raw)}<span class="source-label">{#if showTitle}<Icon name="file" size={21}/><span>{document.title}</span>{/if}{#if pages > 1 && !raw}<span class="muted">{page + 1} of {pages}</span>{/if}</span>{/if}
    <div class="actions preview-actions">
      {#if raw}<button class="text-button" onclick={toggleMetadata}>Back to preview</button>{/if}
      {#if pages > 1 && !raw}
        <button class="icon-button" aria-label="Previous page" disabled={page === 0} onclick={() => page--}><Icon name="left"/></button>
        <button class="icon-button" aria-label="Next page" disabled={page >= pages - 1} onclick={() => page++}><Icon name="right"/></button>
      {/if}
      {#if !raw}<button class="icon-button" class:active={zoom} aria-label="Zoom document" aria-pressed={zoom} onclick={() => zoom = !zoom}><Icon name="review"/></button>{/if}
      <div class="menu-wrap" bind:this={menuElement}><button class="icon-button" bind:this={menuButton} aria-label="Document options" aria-expanded={menu} onclick={() => menu = !menu}><Icon name="more"/></button>{#if menu}<div class="menu">
        <a href={`/api/documents/${document.id}/file`} target="_blank" rel="noreferrer" onclick={() => menu = false}>Open original</a>
        <a href={`/api/documents/${document.id}/file?download=true`} onclick={() => menu = false}>Download document</a>
        <button onclick={() => {menu = false; void toggleMetadata();}}>{raw ? 'Show preview' : 'Raw JSON'}</button>
        <button onclick={() => {menu = false; void frame.requestFullscreen();}}>Expand document</button>
      </div>{/if}</div>
    </div>
  </div>
  <div class="page-canvas" aria-busy={loading}>
    {#if raw}{#if metadataError}<div class="empty" role="alert">{metadataError}</div>{:else}<pre class="metadata-json">{metadata || 'Loading metadata…'}</pre>{/if}
    {:else if error}<div class="empty" role="alert"><p>{error}</p><button onclick={load}>Try again</button></div>
    {:else if loading}<div class="empty">Loading document…</div>
    {:else if image}<img class="pdf-page" src={image} alt={`${document.title}, page ${page + 1}`} onerror={() => error = 'Could not load this page. Restore the source file and try again.'} />
    {:else}<div class="sheet-scroll"><table class="sheet"><tbody>{#each rows as row}<tr>{#each row as cell}<td>{cell}</td>{/each}</tr>{/each}</tbody></table></div>{/if}
  </div>
</div>
