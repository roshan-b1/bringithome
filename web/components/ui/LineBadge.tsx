import type { Line } from '@/contracts/api'
import styles from './LineBadge.module.css'

// A MARTA line, named in words with its colour beside it (never colour alone: 1 in 12 men can't tell Red from
// Green). Colours are the schedule feed's route_color, tokens --line-*.
const NAME: Record<Line, string> = { RED: 'Red', GOLD: 'Gold', BLUE: 'Blue', GREEN: 'Green' }

export const lineName = (line: Line, withLine = true): string => (withLine ? `${NAME[line]} Line` : NAME[line])
export const lineColor = (line: Line): string => `var(--line-${line.toLowerCase()})`

export function LineBadge({ line, withLine = true, className }: { line: Line; withLine?: boolean; className?: string }) {
  return (
    <span className={`${styles.badge} ${className ?? ''}`}>
      <span className={styles.swatch} style={{ background: lineColor(line) }} aria-hidden="true" />
      {lineName(line, withLine)}
    </span>
  )
}
