// Service Worker for Nova PWA with smart caching
const CACHE_NAME = 'nova-v5';  // Includes push notification handlers
const urlsToCache = [
  '/',
  '/static/css/main.css',
  '/static/js/utils.js',
  '/static/js/view-preferences.js',
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

self.addEventListener('push', (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch (e) {
      payload = { body: event.data.text() };
    }
  }

  const title = payload.title || 'Nova';
  const options = {
    body: payload.body || 'Task status updated.',
    tag: payload.tag || 'nova-task',
    icon: '/static/images/icon-192x192.png',
    badge: '/static/images/icon-192x192.png',
    data: payload.data || { url: '/' },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const targetUrl = event.notification?.data?.url || '/';
  event.waitUntil((async () => {
    const target = new URL(targetUrl, self.location.origin);
    const clientsList = await clients.matchAll({ type: 'window', includeUncontrolled: true });

    for (const client of clientsList) {
      try {
        const clientUrl = new URL(client.url);
        if (clientUrl.origin !== target.origin) continue;

        if ('navigate' in client) {
          await client.navigate(target.href);
        }
        if ('focus' in client) {
          await client.focus();
        }
        return;
      } catch (e) {
        // Ignore malformed URLs and continue to next client.
      }
    }

    if (clients.openWindow) {
      await clients.openWindow(target.href);
    }
  })());
});
