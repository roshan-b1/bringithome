import type { MetadataRoute } from 'next'

// The web app manifest (served as /manifest.webmanifest): installing "bring it home" on a phone gives a full-screen
// app with the mark as its icon. start_url is the live app; the "Recorded example" shortcut (a long press on the
// icon, where the launcher supports it) opens the offline demo, which public/sw.js serves with no network.
export const dynamic = 'force-static'

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: '/',
    name: 'bring it home',
    short_name: 'bring it home',
    description: 'Left something on a MARTA bus or train? Find the one you were on, see where it is now, and get a calm plan.',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    background_color: '#ffffff',
    theme_color: '#14213D',
    lang: 'en-US',
    categories: ['travel', 'utilities'],
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icons/maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
      { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
    shortcuts: [
      {
        name: 'Recorded example',
        short_name: 'Example',
        description: 'A recorded ride, replayed with no connection needed.',
        url: '/?mode=offline&set=hero-rail',
        icons: [{ src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' }],
      },
    ],
  }
}
