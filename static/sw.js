const CACHE_NAME = 'droste-stream-v19';
const ASSETS = [
  '/static/index.html',
  '/static/pair.html',
  '/static/manifest.json',
  '/static/droste-icon-180.png',
  '/static/droste-icon-192.png',
  '/static/droste-icon-512.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  // ストリーミングとAPIリクエストはキャッシュしないように除外する
  const url = new URL(e.request.url);
  if (
    url.pathname === '/stream' ||
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/pair')
  ) {
    return;
  }

  if (url.origin !== self.location.origin) {
    return;
  }

  // 更新を確実に反映するためネットワーク優先。オフライン時だけキャッシュへ戻る。
  e.respondWith(
    fetch(e.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(e.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(e.request))
  );
});
