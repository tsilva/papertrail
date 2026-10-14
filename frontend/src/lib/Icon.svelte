<script lang="ts">
  import file from '@phosphor-icons/core/assets/regular/file-text.svg';
  import review from '@phosphor-icons/core/assets/regular/magnifying-glass.svg';
  import left from '@phosphor-icons/core/assets/regular/caret-left.svg';
  import right from '@phosphor-icons/core/assets/regular/caret-right.svg';
  import check from '@phosphor-icons/core/assets/regular/check.svg';
  import close from '@phosphor-icons/core/assets/regular/x.svg';
  import more from '@phosphor-icons/core/assets/regular/dots-three.svg';
  import download from '@phosphor-icons/core/assets/regular/download-simple.svg';
  import arrow from '@phosphor-icons/core/assets/regular/arrow-right.svg';
  import expand from '@phosphor-icons/core/assets/regular/arrows-out.svg';
  const icons = { file, review, left, right, check, close, more, download, arrow, expand };
  let { name, size = 22 }: { name: keyof typeof icons; size?: number } = $props();
</script>

<span class="icon" style:width="{size}px" style:height="{size}px" style:mask-image={`url("${icons[name]}")`} aria-hidden="true"></span>

<style>
  .icon { display: inline-block; flex: 0 0 auto; background: currentColor; mask-size: contain; mask-repeat: no-repeat; mask-position: center; }
</style>
