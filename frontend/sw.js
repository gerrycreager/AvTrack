/**
 * AvTrack Service Worker
 * Network-first for everything except the install icons -- this is live
 * aircraft tracking data (positions, ops board, WS traffic), so nothing
 * involving /api, /ws, or the app shell itself may ever be served stale.
 * Mirrors CAP WxCOP's static/sw.js pattern.
 */

const CACHE_NAME = 'avtrack-v1';

const PRECACHE = [
    '/icons/icon-192.png',
    '/icons/icon-512.png',
    '/icons/icon-512-maskable.png',
];

const NETWORK_ONLY = [
    /\/api\//,
    /\/ws/,
    /\.html$/,
    /\.json$/,
    /\/src\//,
];

self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(function(cache) { return cache.addAll(PRECACHE); })
            .then(function() { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(keys) {
            return Promise.all(
                keys.filter(function(k) { return k !== CACHE_NAME; })
                    .map(function(k) { return caches.delete(k); })
            );
        }).then(function() { return self.clients.claim(); })
    );
});

self.addEventListener('fetch', function(event) {
    var url = event.request.url;

    for (var i = 0; i < NETWORK_ONLY.length; i++) {
        if (NETWORK_ONLY[i].test(url)) {
            event.respondWith(fetch(event.request));
            return;
        }
    }

    if (event.request.mode === 'navigate') {
        event.respondWith(fetch(event.request));
        return;
    }

    // Icons only -- cache first, they never change
    event.respondWith(
        caches.match(event.request).then(function(cached) {
            return cached || fetch(event.request);
        })
    );
});
