/// <reference types="vitest" />
import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';
import { configDefaults } from 'vitest/config';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [preact()],
  // Vitest: jsdom for component tests, setup file wires
  // @testing-library/jest-dom matchers + Tauri IPC stubs.
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    // Local advisory-agent worktrees contain complete repository clones.  The
    // default Vitest globs otherwise discover every cloned test again, making
    // one suite run N+1 times and eventually hanging the worker pool.
    exclude: [
      ...configDefaults.exclude,
      '**/.claude/worktrees/**',
      // Generated products are separate dependency/runtime roots.  Their own
      // tests run in delivery validation and the backend matrix clean room;
      // importing them into the Preact desktop suite mixes React instances.
      '**/.signalos/**',
    ],
  },
  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  // prevent vite from obscuring rust errors
  clearScreen: false,
  // tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
  },
  // to make use of `TAURI_DEBUG` and other env variables
  // https://tauri.studio/v1/api/config#buildconfig.beforedevcommand
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    // Tauri supports es2021
    target: ['es2021', 'chrome100', 'safari13'],
    // don't minify for debug builds
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    // produce sourcemaps for debug builds
    sourcemap: !!process.env.TAURI_DEBUG,
  },
});
