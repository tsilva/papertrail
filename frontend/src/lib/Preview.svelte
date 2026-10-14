<script lang="ts">
  import { onMount } from 'svelte';
  import Icon from './Icon.svelte';
  import type { Document } from './format';
  let { document }: { document: Document } = $props();
  let page = $state(0), pages = $state(1), image = $state(''), rows = $state<string[][]>([]);
  let loading = $state(true), error = $state(''), zoom = $state(false);
  let frame: HTMLDivElement;
  let request = 0;
  async function load() {
    const current = ++request;
    loading = true; error = '';
    try {
      const response = await fetch(`/api/documents/${document.id}/preview?page=${page}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not load this document.');
      if (current !== request) return;
      pages = data.pages; image = data.image || ''; rows = data.rows || [];
    } catch (e) { if (current === request) error = e instanceof Error ? e.message : 'Preview unavailable.'; }
    finally { if (current === request) loading = false; }
  }
  $effect(() => { document.id; page; void load(); });
  onMount(() => () => { request++; });
</script>
<div class="source" bind:this={frame} class:zoom>
  <div class="source-tools" class:multiple={pages > 1}>
    <span class="source-label"><Icon name="file" size={21}/><span>{document.title}</span>{#if pages > 1}<span class="muted">· {page + 1} of {pages}</span>{/if}</span>
    <div class="actions">
      {#if pages > 1}
        <button class="icon-button" aria-label="Previous page" disabled={page === 0} onclick={() => page--}><Icon name="left"/></button>
        <button class="icon-button" aria-label="Next page" disabled={page >= pages - 1} onclick={() => page++}><Icon name="right"/></button>
      {/if}
      <button class="icon-button" class:active={zoom} aria-label="Zoom document" aria-pressed={zoom} onclick={() => zoom = !zoom}><Icon name="review"/></button>
      <button class="icon-button" aria-label="Expand document" onclick={() => frame.requestFullscreen()}><Icon name="expand"/></button>
    </div>
  </div>
  <div class="page-canvas" aria-busy={loading}>
    {#if error}<div class="empty" role="alert"><p>{error}</p><button onclick={load}>Try again</button></div>
    {:else if loading}<div class="empty">Loading document…</div>
    {:else if image}<img class="pdf-page" src={image} alt={`${document.title}, page ${page + 1}`} />
    {:else}<div class="sheet-scroll"><table class="sheet"><tbody>{#each rows as row}<tr>{#each row as cell}<td>{cell}</td>{/each}</tr>{/each}</tbody></table></div>{/if}
  </div>
</div>
