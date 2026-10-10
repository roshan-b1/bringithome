import type { Metadata, Viewport } from 'next'
import { Schibsted_Grotesk } from 'next/font/google'
import type { ReactNode } from 'react'
import { t } from '@/lib/copy'
import './globals.css'

// Schibsted Grotesk: the closest Google match to the wordmark (double-story "g", slanted "t", weights to 900;
// Familjen Grotesk's Google build has only a single-story "g" and stops at 700). Downloaded at build time and
// self-hosted by next/font, so the browser never calls Google.
const brand = Schibsted_Grotesk({
  subsets: ['latin'],
  variable: '--font-brand',
  display: 'swap',
})

export const metadata: Metadata = {
  metadataBase: new URL('https://bringit.casa'),
  title: 'bring it home',
  description: `${t('home.question')} ${t('home.promise')}`,
  applicationName: 'bring it home',
  openGraph: {
    title: 'bring it home',
    description: `${t('home.question')} ${t('home.promise')}`,
    url: 'https://bringit.casa',
    siteName: 'bring it home',
    type: 'website',
  },
  formatDetection: { telephone: false }, // phone numbers are explicit tel: links, never auto-detected
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover', // draw under the notch and home indicator; the screens pad with env(safe-area-inset-*)
  themeColor: '#ffffff',
  colorScheme: 'light',
}

// A visit saved in this tab (a reload mid-flow) resumes on its own step. Until the resume runs, hide the
// prerendered home screen so it doesn't flash first; anything that fails here just shows the home screen.
const RESUME_GUARD = `try{if(sessionStorage.getItem('bring-it-home:v1'))document.documentElement.dataset.resuming='1'}catch(e){}`

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={brand.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: RESUME_GUARD }} />
        {/* the reveal after 5 s is a safety net: if the script bundle never runs, the page must not stay blank */}
        <style>{`html[data-resuming] body{visibility:hidden;animation:bih-reveal 0s 5s forwards}@keyframes bih-reveal{to{visibility:visible}}`}</style>
      </head>
      <body>{children}</body>
    </html>
  )
}
