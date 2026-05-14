import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/app/review/',
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/review': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    exclude: ['tests/**', 'node_modules/**', 'dist/**'],
  },
});
