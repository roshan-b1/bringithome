// Favicon, apple-touch-icon and the share image, all drawn from the mark in lib/brand.ts (dev-time tool, run by hand
// after the mark changes: `node scripts/make_icons.mjs`). Chromium from Playwright rasterizes, so no image library
// is needed; the .ico is packed by hand (an ICONDIR header plus PNG entries, which every current browser reads).
//
// Writes into app/ (Next's metadata file conventions, so the layout needs no <link> tags):
//   icon.svg              the mark on a white rounded square, so it reads on dark tab strips too
//   favicon.ico           16, 32 and 48 px
//   apple-icon.png        180 px, white ground (iOS rounds the corners itself)
//   opengraph-image.png   1200x630, the full lockup, for links shared in chat
import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { MARK_SVG_BODY, MARK_VIEWBOX } from '../lib/brand.ts'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const app = join(web, 'app')
const [vx, vy, vw, vh] = MARK_VIEWBOX

// the mark centred in a square with a margin (fraction of the side), on white, optionally with rounded corners
function squareSvg(margin, radius) {
  const side = Math.max(vw, vh) / (1 - 2 * margin)
  const x = vx - (side - vw) / 2
  const y = vy - (side - vh) / 2
  const r = side * radius
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x} ${y} ${side} ${side}">` +
    `<rect x="${x}" y="${y}" width="${side}" height="${side}" rx="${r}" fill="#ffffff"/>${MARK_SVG_BODY}</svg>\n`
  )
}

const iconSvg = squareSvg(0.1, 0.22)
writeFileSync(join(app, 'icon.svg'), iconSvg)

const browser = await chromium.launch()
const page = await browser.newPage({ deviceScaleFactor: 1 })

async function png(svg, w, h) {
  await page.setViewportSize({ width: w, height: h })
  await page.setContent(
    `<html><body style="margin:0;background:transparent"><img style="display:block;width:${w}px;height:${h}px" src="data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}"></body></html>`,
  )
  await page.waitForFunction(() => document.querySelector('img').complete)
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: w, height: h } })
}

// favicon.ico: small sizes get a tighter margin so the line stays thick enough to read at 16 px
const icoSizes = [16, 32, 48]
const icoPngs = []
for (const s of icoSizes) icoPngs.push(await png(squareSvg(s <= 16 ? 0.04 : 0.08, 0.2), s, s))
const header = Buffer.alloc(6 + 16 * icoSizes.length)
header.writeUInt16LE(0, 0)
header.writeUInt16LE(1, 2) // 1 = icon
header.writeUInt16LE(icoSizes.length, 4)
let offset = header.length
icoSizes.forEach((s, i) => {
  const e = 6 + 16 * i
  header.writeUInt8(s, e)
  header.writeUInt8(s, e + 1)
  header.writeUInt8(0, e + 2) // no palette
  header.writeUInt8(0, e + 3)
  header.writeUInt16LE(1, e + 4) // planes
  header.writeUInt16LE(32, e + 6) // bits per pixel
  header.writeUInt32LE(icoPngs[i].length, e + 8)
  header.writeUInt32LE(offset, e + 12)
  offset += icoPngs[i].length
})
writeFileSync(join(app, 'favicon.ico'), Buffer.concat([header, ...icoPngs]))

writeFileSync(join(app, 'apple-icon.png'), await png(squareSvg(0.14, 0), 180, 180))

// share image: the lockup (traced by trace_wordmark.mjs) on white, with the warm paper edge
const lockup = readFileSync(join(web, 'public/brand/lockup.svg'), 'utf8')
await page.setViewportSize({ width: 1200, height: 630 })
await page.setContent(
  `<html><body style="margin:0;width:1200px;height:630px;display:grid;place-items:center;background:#ffffff">` +
    `<img style="width:860px" src="data:image/svg+xml;base64,${Buffer.from(lockup).toString('base64')}">` +
    `<div style="position:absolute;left:0;right:0;bottom:0;height:14px;background:#EF5B2B"></div></body></html>`,
)
await page.waitForFunction(() => document.querySelector('img').complete)
writeFileSync(join(app, 'opengraph-image.png'), await page.screenshot({ clip: { x: 0, y: 0, width: 1200, height: 630 } }))
await browser.close()

const kb = (f) => `${f} ${(readFileSync(join(app, f)).length / 1024).toFixed(1)} KB`
console.log(['icon.svg', 'favicon.ico', 'apple-icon.png', 'opengraph-image.png'].map(kb).join(', '))
