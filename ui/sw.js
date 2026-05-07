/**
 * Service Worker for Petition Verifier PWA
 *
 * Strategy:
 *   - App shell (HTML/assets): cache on install, serve from cache first
 *   - API calls (/auth, /process, /worker, etc.): network first, no caching
 *   - Offline fallback: show offline.html when network unavailable
 */

const CACHE_NAME = 'pv-shell-v7';

const SHELL_ASSETS = [
  '/static/login.html',
  '/static/worker.html',
  '/static/dashboard.html',
  '/static/manifest.json',
  '/static/icon.svg',
];

// ── Install: pre-cache the app shell ─────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

// ── Activate: delete old caches ───────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch: route requests ─────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // API calls — always go to network, never cache
  if (!url.pathname.startsWith('/static/')) {
    event.respondWith(networkOnly(event.request));
    return;
  }

  // App shell — cache first, fall back to network
  event.respondWith(cacheFirst(event.request));
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('<h2>You are offline</h2><p>Please reconnect to continue.</p>', {
      headers: { 'Content-Type': 'text/html' },
    });
  }
}

async function networkOnly(request) {
  try {
    return await fetch(request);
  } catch {
    return new Response(
      JSON.stringify({ detail: 'You are offline. Please reconnect and try again.' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}
