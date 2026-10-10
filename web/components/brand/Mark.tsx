import { MARK_LINE, MARK_TAG, MARK_VIEWBOX, MARK_WHEEL } from '@/lib/brand'

// The logo mark as inline SVG (geometry in lib/brand.ts, measured from the logo PNG). Inline so it needs no request,
// stays crisp at any size and takes its colours from tokens. Decorative by default; pass `title` when it is the
// only thing naming the product in that spot.
export function Mark({ size = 32, title, className }: { size?: number | string; title?: string; className?: string }) {
  const [x, y, w, h] = MARK_VIEWBOX
  const height = typeof size === 'number' ? `${size}px` : size
  return (
    <svg
      viewBox={`${x} ${y} ${w} ${h}`}
      style={{ height, width: 'auto', aspectRatio: `${w} / ${h}` }}
      className={className}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}
      <path d={MARK_LINE} fill="none" stroke="var(--orange, #EF5B2B)" strokeWidth={70} strokeLinejoin="round" />
      <path d={MARK_TAG} fill="var(--orange, #EF5B2B)" fillRule="evenodd" />
      <path d={MARK_WHEEL} fill="var(--navy, #14213D)" fillRule="evenodd" />
    </svg>
  )
}
