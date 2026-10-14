import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
export default defineConfig({
  plugins: [svelte()],
  build: { outDir: '../tools/static/dashboard', emptyOutDir: true },
  server: {
    host: '127.0.0.1',
    proxy: { '/api': process.env.PAPERTRAIL_API_URL || 'http://127.0.0.1:7864' },
  },
});
