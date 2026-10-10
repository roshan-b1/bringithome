// The wordmark, traced from the logo PNG into SVG by scripts/trace_wordmark.mjs (the real letterforms, including
// the flag-eared "g" that no Google font has). Served as a static file so its ~9 KB of path data stays out of the
// JS bundle; width and height are set so nothing shifts while it loads.
const SIZES = {
  stacked: { src: '/brand/wordmark.svg', w: 1121.3, h: 503 }, // "bring it" over "home", as in the logo
  line: { src: '/brand/wordmark-line.svg', w: 2137.6, h: 286.2 }, // one line, for tight headers
} as const

export function Wordmark({
  variant = 'stacked',
  height = 48,
  className,
  decorative = false,
}: {
  variant?: keyof typeof SIZES
  height?: number
  className?: string
  /** true when the product name is already said next to it (aria) */
  decorative?: boolean
}) {
  const s = SIZES[variant]
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a static SVG; next/image adds nothing in an export
    <img
      src={s.src}
      alt={decorative ? '' : 'bring it home'}
      width={Math.round((height * s.w) / s.h)}
      height={height}
      className={className}
      decoding="async"
      draggable={false}
    />
  )
}
