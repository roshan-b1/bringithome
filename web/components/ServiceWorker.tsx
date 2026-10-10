'use client'

import { useEffect } from 'react'

// Registers public/sw.js in production builds only (next dev never gets a worker, so a stale cache can't hide a
// change while building). The worker caches the app and the recorded sets, never /api (see public/sw.js).
// updateViaCache 'none': the browser always asks the server for sw.js itself, so a new build is seen at once.
export function ServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production' || !('serviceWorker' in navigator)) return
    navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).catch(() => {
      // no worker (private mode, an old browser): the app works the same, it just needs a connection to open
    })
  }, [])
  return null
}
