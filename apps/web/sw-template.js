const CACHE = 'supermarket-offline-shell-v1';
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const response = await fetch('/index.html', { cache: 'no-store' });
    if (!response.ok) throw new Error('Offline app shell unavailable');
    const html = await response.text();
    const assets = [...html.matchAll(/(?:src|href)=["'](\/assets\/[^"']+)["']/g)].map((match) => match[1]);
    const cache = await caches.open(CACHE);
    await cache.addAll(['/index.html', '/offline-pos', ...assets]);
  })());
});
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (url.pathname !== '/offline-pos' && !url.pathname.startsWith('/assets/')) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const saved = await cache.match(event.request);
    if (saved) return saved;
    const response = await fetch(event.request);
    if (response.ok) await cache.put(event.request, response.clone());
    return response;
  })());
});
