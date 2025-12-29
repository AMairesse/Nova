// Service Worker for Nova PWA with smart caching
const CACHE_NAME = 'nova-v3';  // Updated version for new caching logic
const urlsToCache = [
  '/',
  '/static/css/main.css',
  '/static/js/utils.js',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.7/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.7/dist/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/htmx.org@2.0.6/dist/htmx.min.js'
];

let config = {
  debug: false,
  cacheMaxAge: 3 * 24 * 60 * 60 * 1000  // 3 days in ms
};

// Listen for config updates from main thread
self.addEventListener('message', (event) => {
  if (event.data.type === 'SET_CONFIG') {
    config = { ...config, ...event.data };
    console.log('SW config updated:', config);
  }
});

// Install Service Worker
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
  );
});

// Fetch event with smart caching
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // never intercept non-GET

  const url = new URL(req.url);
  const isStatic = url.pathname.startsWith('/static/')
    || url.origin.startsWith('https://cdn.jsdelivr.net');

  if (!isStatic) return; // let network handle

  event.respondWith(
    caches.open(CACHE_NAME).then(cache => {
      return cache.match(req).then(cached => {
        const now = Date.now();

        // In debug mode, always fetch fresh
        if (config.debug) {
          return fetchAndCache(req, cache);
        }

        // If cached, check age
        if (cached) {
          const cacheDate = new Date(cached.headers.get('sw-cache-date') || 0);
          const age = now - cacheDate.getTime();

          // If cache is fresh (< 3 days), use it
          if (age < config.cacheMaxAge) {
            return cached;
          }

          // Cache is old, try to refresh
          return fetchAndCache(req, cache).catch(() => cached);
        }

        // Not cached, fetch and cache
        return fetchAndCache(req, cache);
      });
    })
  );
});

function fetchAndCache(request, cache) {
  return fetch(request).then(response => {
    if (response.ok) {
      // Clone response and add cache timestamp
      const responseClone = response.clone();
      const responseWithTimestamp = new Response(responseClone.body, {
        status: responseClone.status,
        statusText: responseClone.statusText,
        headers: {
          ...Object.fromEntries(responseClone.headers.entries()),
          'sw-cache-date': new Date().toISOString()
        }
      });
      cache.put(request, responseWithTimestamp);
    }
    return response;
  });
}

// Activate event
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});
