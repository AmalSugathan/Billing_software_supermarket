import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react(), {
    name: 'versioned-offline-shell',
    generateBundle(_, bundle) {
      const version = createHash('sha256').update(Object.keys(bundle).sort().join('|')).digest('hex').slice(0, 16);
      const source = readFileSync(new URL('./sw-template.js', import.meta.url), 'utf8').replace('supermarket-offline-shell-v1', 'supermarket-offline-shell-' + version);
      this.emitFile({ type: 'asset', fileName: 'sw.js', source });
    },
  }],
  server: {
    host: '127.0.0.1',
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  preview: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    clearMocks: true,
  },
});
