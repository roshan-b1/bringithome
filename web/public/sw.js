// The service worker for the installed app (registered in production builds only, by components/ServiceWorker).
//
// What it stores: the app's own files from the static export and the recorded fixture sets in public/fixtures, so
// the installed app opens and runs the recorded example (?mode=offline&set=hero-rail) in airplane mode. All of it is
// the same for every visitor: code, icons, map outlines and public recorded MARTA data.
// What it never stores: anything under /api/. Those requests are not touched at all (the browser sends them to the
// network as if there were no worker), so a rider's session, clues and plan never land in a cache on the phone.
//
// Updates: scripts/build_sw.mjs rewrites the BUILD line below in out/sw.js with this build's version and file list,
// so every build is a byte-different worker. The new one precaches into its own versioned cache while the old one
// keeps serving; it takes over when every tab of the app has closed (no swap under a rider mid-plan), then deletes
// the old caches. The first install claims the open page at once, so airplane mode works without a reload.

/* eslint-disable no-restricted-globals */
const BUILD = /*BUILD*/ { version: 'dev', files: [] } /*END*/

const PREFIX = 'bring-it-home-'
const CACHE = `${PREFIX}${BUILD.version}`

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // cache: 'reload' skips the HTTP cache, so a stale copy from before this build is never precached
      cache.addAll(BUILD.files.map((url) => new Request(url, { cache: 'reload' }))),
    ),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys()
      await Promise.all(keys.filter((k) => k.startsWith(PREFIX) && k !== CACHE).map((k) => caches.delete(k)))
      await self.clients.claim()
    })(),
  )
})

const isApi = (url) => url.pathname === '/api' || url.pathname.startsWith('/api/')

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)
  // network only: the API (rider data), other origins (map tiles, which need a connection anyway), and writes
  if (url.origin !== self.location.origin || isApi(url)) return
  if (req.method !== 'GET' && req.method !== 'HEAD') return
  event.respondWith(answer(req, url))
})

async function answer(req, url) {
  const cache = await caches.open(CACHE)
  if (req.mode === 'navigate') {
    // one page app: every visit is index.html (or its route's own file), whatever the query (?mode=offline&set=...)
    const page = (await cache.match(url.pathname, { ignoreSearch: true })) ?? (await cache.match('/', { ignoreSearch: true }))
    if (page) return page
    return fetch(req)
  }
  // cache first; fixture probes are HEAD requests, answered from the cached GET
  const hit = await cache.match(url.pathname, { ignoreSearch: true, ignoreMethod: true })
  if (hit) return req.method === 'HEAD' ? new Response(null, { status: hit.status, headers: hit.headers }) : hit
  return fetch(req)
}

self.addEventListener('message', (event) => {
  // a page may ask a waiting worker to take over now (not used by the app today; kept for a future "update" banner)
  if (event.data === 'skip-waiting') self.skipWaiting()
})
