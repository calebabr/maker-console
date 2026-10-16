import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import { readFileSync } from 'node:fs';

const APP_VERSION = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')).version;

const THEME_COLOR = '#0f141b'; // Midnight Slate background (the default theme)

export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(APP_VERSION) },
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'auto',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'Keel',
        short_name: 'Keel',
        description: 'Keel: your strategies. One tap away.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: THEME_COLOR,
        theme_color: THEME_COLOR,
        icons: [
          { src: 'icon.svg', sizes: 'any', type: 'image/svg+xml' },
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // App shell only. API responses are never cached by the service worker
        // (they are cross-origin and carry a Bearer token); the app keeps the last
        // API data in sessionStorage for offline display instead.
        globPatterns: ['**/*.{js,css,html,svg,png,ico,webmanifest}'],
        navigateFallback: 'index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/healthz/],
        runtimeCaching: [],
        // Web push + notification-click deep links (UX item 45); public/push-sw.js.
        importScripts: ['push-sw.js'],
        cleanupOutdatedCaches: true,
      },
      devOptions: { enabled: false },
    }),
  ],
  server: { port: 5173 },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    css: false,
    env: { VITE_API_BASE_URL: 'http://api.test' },
  },
});
