import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Relative base so assets resolve wherever the site is mounted — e.g.
// livekit.com/benchmarks/eot-bench, a *.pages.github.io root, or a local
// preview. The app is a single page with no client-side routing, so relative
// asset URLs are safe.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
});
