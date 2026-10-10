// Vectorizes the navy wordmark of brand/bring-it-home-logo.png into SVG paths (dev-time tool, run once by hand:
// `node scripts/trace_wordmark.mjs`). Why trace instead of shipping a PNG crop: the lockup has to stay crisp on a
// 3x phone and on a projector, and an SVG is smaller than a PNG big enough for both.
//
// How: Chromium (already here for Playwright) decodes the PNG; marching squares runs on the alpha channel at the
// 0.5 iso-line, interpolating along anti-aliased edges, so contours land at sub-pixel accuracy; loops are chained
// orientation-free and simplified with Ramer-Douglas-Peucker (0.35 px). Fill rule evenodd makes counters (the holes
// in b, o, e, g) come out right without tracking winding.
//
// Writes public/brand/wordmark.svg (two lines, as in the logo), wordmark-line.svg (one line, for headers) and
// lockup.svg (mark + wordmark + domain, the logo's own coordinates, for the comparison screenshot).
import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { MARK_SVG_BODY, MARK_VIEWBOX } from '../lib/brand.ts'

const here = dirname(fileURLToPath(import.meta.url))
const web = join(here, '..')
const png = join(web, 'public/brand/bring-it-home-logo.png')
const NAVY = '#14213D'
const X_MIN = 700 // everything right of the mark is wordmark

async function alphaGrid() {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  const b64 = readFileSync(png).toString('base64')
  await page.setContent(`<img id=i src="data:image/png;base64,${b64}">`)
  await page.waitForFunction(() => document.getElementById('i').complete)
  const { w, h, a } = await page.evaluate((xMin) => {
    const img = document.getElementById('i')
    const c = document.createElement('canvas')
    c.width = img.naturalWidth
    c.height = img.naturalHeight
    const ctx = c.getContext('2d')
    ctx.drawImage(img, 0, 0)
    const d = ctx.getImageData(0, 0, c.width, c.height).data
    const a = []
    for (let y = 0; y < c.height; y++)
      for (let x = 0; x < c.width; x++) {
        const i = (y * c.width + x) * 4
        // navy coverage only: the white holes of the mark and the orange line never count
        const navy = d[i] < 120 && d[i + 1] < 120 && d[i + 2] < 160
        a.push(x >= xMin && navy ? d[i + 3] : 0)
      }
    return { w: c.width, h: c.height, a }
  }, X_MIN)
  await browser.close()
  return { w, h, a: Float32Array.from(a, (v) => v / 255) }
}

function marchingSquares({ w, h, a }) {
  // samples at pixel centres; one cell between four samples. The image is transparent at its border, so every
  // contour closes.
  const at = (x, y) => (x < 0 || y < 0 || x >= w || y >= h ? 0 : a[y * w + x])
  const lerp = (p, q) => (0.5 - p) / (q - p)
  const segs = []
  for (let y = -1; y < h; y++)
    for (let x = -1; x < w; x++) {
      const tl = at(x, y), tr = at(x + 1, y), br = at(x + 1, y + 1), bl = at(x, y + 1)
      const k = (tl > 0.5 ? 8 : 0) | (tr > 0.5 ? 4 : 0) | (br > 0.5 ? 2 : 0) | (bl > 0.5 ? 1 : 0)
      if (k === 0 || k === 15) continue
      const E = {
        T: { id: `h${x},${y}`, p: [x + lerp(tl, tr), y] },
        B: { id: `h${x},${y + 1}`, p: [x + lerp(bl, br), y + 1] },
        L: { id: `v${x},${y}`, p: [x, y + lerp(tl, bl)] },
        R: { id: `v${x + 1},${y}`, p: [x + 1, y + lerp(tr, br)] },
      }
      const centre = (tl + tr + br + bl) / 4 > 0.5
      const pairs = {
        1: ['LB'], 2: ['BR'], 3: ['LR'], 4: ['TR'], 6: ['TB'], 7: ['TL'], 8: ['TL'], 9: ['TB'],
        11: ['TR'], 12: ['LR'], 13: ['BR'], 14: ['LB'],
        5: centre ? ['TL', 'BR'] : ['TR', 'LB'],
        10: centre ? ['TR', 'LB'] : ['TL', 'BR'],
      }[k]
      for (const [m, n] of pairs) segs.push([E[m], E[n]])
    }
  // chain: every edge point is shared by exactly two segments
  const byEdge = new Map()
  segs.forEach((s, i) => s.forEach((e) => (byEdge.get(e.id) ?? byEdge.set(e.id, []).get(e.id)).push(i)))
  const used = new Uint8Array(segs.length)
  const loops = []
  for (let s0 = 0; s0 < segs.length; s0++) {
    if (used[s0]) continue
    const loop = [segs[s0][0].p]
    let s = s0, end = segs[s0][1]
    used[s0] = 1
    for (;;) {
      loop.push(end.p)
      const next = byEdge.get(end.id).find((i) => !used[i])
      if (next === undefined) break
      used[next] = 1
      end = segs[next][0].id === end.id ? segs[next][1] : segs[next][0]
      s = next
    }
    if (loop.length > 8) loops.push(loop)
  }
  return loops
}

function rdp(pts, eps) {
  if (pts.length < 3) return pts
  const [ax, ay] = pts[0], [bx, by] = pts[pts.length - 1]
  const dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1
  let worst = 0, at = 0
  for (let i = 1; i < pts.length - 1; i++) {
    const d = Math.abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / len
    if (d > worst) (worst = d), (at = i)
  }
  if (worst <= eps) return [pts[0], pts[pts.length - 1]]
  return [...rdp(pts.slice(0, at + 1), eps).slice(0, -1), ...rdp(pts.slice(at), eps)]
}

function simplifyLoop(loop, eps) {
  // split a closed loop at its two most distant points so RDP has fixed ends
  let far = 0, fd = 0
  for (let i = 1; i < loop.length; i++) {
    const d = Math.hypot(loop[i][0] - loop[0][0], loop[i][1] - loop[0][1])
    if (d > fd) (fd = d), (far = i)
  }
  return [...rdp(loop.slice(0, far + 1), eps).slice(0, -1), ...rdp([...loop.slice(far), loop[0]], eps).slice(0, -1)]
}

const bbox = (loop) => {
  const xs = loop.map((p) => p[0]), ys = loop.map((p) => p[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}
const r1 = (v) => Math.round(v * 10) / 10
// +0.5: samples sit at pixel centres
const pathOf = (loops, dx = 0, dy = 0) =>
  loops
    .map((l) => 'M' + l.map(([x, y]) => `${r1(x + 0.5 + dx)} ${r1(y + 0.5 + dy)}`).join('L') + 'Z')
    .join('')

const grid = await alphaGrid()
const loops = marchingSquares(grid).map((l) => simplifyLoop(l, 0.35))
const groups = { line1: [], line2: [], domain: [] }
for (const l of loops) {
  const [, top, , bottom] = bbox(l)
  if (top > 640) groups.domain.push(l)
  else if (top > 340) groups.line2.push(l) // "home" (and its counters); the g, descender included, starts at 110
  else groups.line1.push(l)
}
const box = (ls) => ls.map(bbox).reduce((m, b) => [Math.min(m[0], b[0]), Math.min(m[1], b[1]), Math.max(m[2], b[2]), Math.max(m[3], b[3])])
const [l1x0, l1y0, l1x1] = box(groups.line1)
const [l2x0, , l2x1, l2y1] = box(groups.line2)
// baselines: the bottom of the "i" stems (line 1) and of "home" (line 2)
const base1 = Math.max(...groups.line1.map(bbox).filter((b) => b[0] > 1700 && b[0] < 1800).map((b) => b[3]))
const base2 = l2y1
const pad = 2
const stacked = [l1x0 - pad, l1y0 - pad, Math.max(l1x1, l2x1) - l1x0 + 2 * pad, base2 - l1y0 + 2 * pad]
const svg = (vb, body, title) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${vb.map(r1).join(' ')}" role="img" aria-label="${title}"><title>${title}</title>${body}</svg>\n`
const navyPath = (d) => `<path fill="${NAVY}" fill-rule="evenodd" d="${d}"/>`

writeFileSync(join(web, 'public/brand/wordmark.svg'), svg(stacked, navyPath(pathOf([...groups.line1, ...groups.line2])), 'bring it home'))

// one line: "home" moved up onto line 1's baseline, one word space (the logo's own "bring"/"it" gap) to the right
const gGap = 85
const dx = l1x1 + gGap - l2x0, dy = base1 - base2
const line = [l1x0 - pad, l1y0 - pad, l2x1 + dx - l1x0 + 2 * pad, Math.max(base1, ...groups.line1.map((l) => bbox(l)[3])) - l1y0 + 2 * pad]
writeFileSync(join(web, 'public/brand/wordmark-line.svg'), svg(line, `<path fill="${NAVY}" fill-rule="evenodd" d="${pathOf(groups.line1)}${pathOf(groups.line2, dx, dy)}"/>`, 'bring it home'))

const all = [MARK_VIEWBOX[0], 70, 1930 - MARK_VIEWBOX[0], 690]
writeFileSync(
  join(web, 'public/brand/lockup.svg'),
  svg(all, MARK_SVG_BODY + navyPath(pathOf([...groups.line1, ...groups.line2, ...groups.domain])), 'bring it home, bringit.casa'),
)
const kb = (f) => (readFileSync(join(web, 'public/brand', f)).length / 1024).toFixed(1) + ' KB'
console.log(`loops ${loops.length} (line1 ${groups.line1.length}, line2 ${groups.line2.length}, domain ${groups.domain.length})`)
console.log(`wordmark.svg ${kb('wordmark.svg')}, wordmark-line.svg ${kb('wordmark-line.svg')}, lockup.svg ${kb('lockup.svg')}`)
console.log(`stacked viewBox ${stacked.map(r1)}; line viewBox ${line.map(r1)}; baselines ${r1(base1)} ${r1(base2)}`)
