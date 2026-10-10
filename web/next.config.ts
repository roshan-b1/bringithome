import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { NextConfig } from 'next'
import { PHASE_DEVELOPMENT_SERVER } from 'next/constants'

// Production is a static export behind Caddy: Caddy serves web/out and proxies /api to FastAPI on the same origin
// (ARCHITECTURE.md decisions 12 and 17), so the client always calls relative /api/... and there is no Node server.
//
// `output: 'export'` makes `next dev` reject rewrites, so the export setting and the dev proxy never meet: the dev
// server gets the /api rewrite to the local API (no CORS dance), and every build gets the export. Runtime modes
// (?mode=, ?set=, ?stage=) are read in the browser, never here (decision 12: no build-time endpoint flags).
const API_ORIGIN = process.env.BACKTRACK_API_ORIGIN ?? 'http://127.0.0.1:8000'

const shared: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // next/image's default loader needs a server; the export ships images as they are
  images: { unoptimized: true },
  // /route/ -> out/route/index.html, which python -m http.server (the offline presenter laptop) and Caddy's
  // file_server both serve without try_files rules
  trailingSlash: true,
  // dev must not write AGENTS.md into the shared repo while several dev servers run at once
  agentRules: false,
  // web/ is the whole app (nothing is imported from outside it); pin the root so a stray lockfile higher up the
  // tree is never mistaken for the workspace root
  turbopack: { root: dirname(fileURLToPath(import.meta.url)) },
}

export default function config(phase: string): NextConfig {
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    return {
      ...shared,
      // the dev harnesses (app/dev/*/page.dev.tsx: the component gallery and the intake, results and plan
      // harnesses) are routes only here; a build doesn't see them at all, so none of their code ships
      pageExtensions: ['dev.tsx', 'tsx', 'ts'],
      // the floating dev badge sits over the bottom-left of a phone screen, right where screenshots look
      devIndicators: false,
      // with trailingSlash on, Next would 308 /api/health to /api/health/ before rewriting, and FastAPI has no
      // trailing-slash routes; the redirect only matters for the export, so dev skips it
      skipTrailingSlashRedirect: true,
      async rewrites() {
        return [{ source: '/api/:path*', destination: `${API_ORIGIN}/api/:path*` }]
      },
    }
  }
  return { ...shared, output: 'export' }
}
