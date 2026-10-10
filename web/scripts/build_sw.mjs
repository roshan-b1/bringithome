#!/usr/bin/env node
// After `next build`: write this build's precache list into out/sw.js (public/sw.js's BUILD line), so the installed
// app's service worker caches exactly the files this export serves. The version is a hash of every precached file,
// so an unchanged build keeps its caches and any change installs a new worker.
//
// Precached: every file in out/ (pages, the hashed JS and CSS, the font, icons, the map outlines in geo/ and the
// recorded fixture sets in fixtures/), except sw.js itself, source maps and MapLibre's development worker, which a
// production page never loads. Nothing under /api exists in out/, so nothing from the API can be listed.
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const OUT = fileURLToPath(new URL('../out/', import.meta.url))

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? walk(p) : [p]
  })
}

const skip = (rel) =>
  rel === 'sw.js' ||
  rel.endsWith('.map') ||
  rel.endsWith('.txt') || // the RSC payloads: a static export page loads from its HTML, never from these
  rel.startsWith('_next/static/development/') ||
  // MapLibre's development builds (maplibre-gl-dev, maplibre-gl-worker-dev) are emitted too but never loaded
  /^_next\/static\/media\/maplibre-gl(-worker)?-dev\./.test(rel)

const files = []
const hash = createHash('sha256')
let bytes = 0
for (const abs of walk(OUT).sort()) {
  const rel = relative(OUT, abs).split(sep).join('/')
  const body = readFileSync(abs)
  if (skip(rel)) continue
  // trailingSlash export: foo/index.html is served at /foo/, and index.html at /
  const url = rel === 'index.html' ? '/' : rel.endsWith('/index.html') ? `/${rel.slice(0, -'index.html'.length)}` : `/${rel}`
  files.push(url)
  hash.update(url).update(body)
  bytes += body.length
}

const version = hash.digest('hex').slice(0, 12)
const swPath = join(OUT, 'sw.js')
const sw = readFileSync(swPath, 'utf8')
const BUILD = /\/\*BUILD\*\/[\s\S]*?\/\*END\*\//
if (!BUILD.test(sw)) throw new Error('out/sw.js has no /*BUILD*/ ... /*END*/ line to fill')
writeFileSync(swPath, sw.replace(BUILD, `/*BUILD*/ ${JSON.stringify({ version, files })} /*END*/`))
console.log(`sw.js: version ${version}, ${files.length} files, ${(bytes / 1024 / 1024).toFixed(2)} MB precached`)
