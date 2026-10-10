// The logo mark, redrawn from pixel measurements of brand/bring-it-home-logo.png (2000x829), in that file's own
// coordinates so the lockup can be checked against the PNG one to one. Shared by components/brand/Mark.tsx and
// the trace/icon scripts.
//
// Measured: orange stroke 70 px with round joins (the outer corners are r = 35 = half the stroke); the bottom line
// centre y = 594; walls at x = 230 and 555; roof peak centre (392.5, 200), so the outer peak edge sits at y = 165;
// the key tag is a round end at (114, 594) with a 16 px hole; the navy wheel is a ring at (392.5, 594), r 69.5
// outside and 34 inside. Holes are real holes (fill-rule evenodd), not white paint, so the mark sits on any
// surface. The line starts at x = 141, past the tag hole, and ends at x = 430 under the ring, past the ring's hole
// (392.5 + 34 = 426.5): with butt caps neither end can show through a hole.
export const MARK_VIEWBOX = [79, 164, 512, 500] as const
export const ORANGE = '#EF5B2B'
export const NAVY = '#14213D'
export const MARK_LINE = 'M140 594H230V350L392.5 200L555 350V594H430'
export const MARK_TAG = 'M141 559H114A35 35 0 0 0 114 629H141ZM130 594A16 16 0 1 0 98 594A16 16 0 1 0 130 594Z'
export const MARK_WHEEL =
  'M462 594A69.5 69.5 0 1 1 323 594A69.5 69.5 0 1 1 462 594ZM426.5 594A34 34 0 1 0 358.5 594A34 34 0 1 0 426.5 594Z'

export const MARK_SVG_BODY =
  `<path d="${MARK_LINE}" fill="none" stroke="${ORANGE}" stroke-width="70" stroke-linejoin="round"/>` +
  `<path d="${MARK_TAG}" fill="${ORANGE}" fill-rule="evenodd"/>` +
  `<path d="${MARK_WHEEL}" fill="${NAVY}" fill-rule="evenodd"/>`

export const markSvg = (title = 'bring it home'): string =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${MARK_VIEWBOX.join(' ')}" role="img" aria-label="${title}">` +
  `<title>${title}</title>${MARK_SVG_BODY}</svg>\n`
