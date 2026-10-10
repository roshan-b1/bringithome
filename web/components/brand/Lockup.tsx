import { Mark } from './Mark'
import { Wordmark } from './Wordmark'
import styles from './Lockup.module.css'

// Mark beside the stacked wordmark, in the logo's own proportions: the mark is as tall as the two wordmark lines
// (500 vs 503 units in the PNG) and sits about a third of its height away. Both scale from one CSS variable, so
// the narrowest phones can shrink the whole lockup without distorting either part.
export function Lockup({ height = 88, className }: { height?: number; className?: string }) {
  return (
    <span className={`${styles.lockup} ${className ?? ''}`} style={{ ['--h' as string]: `${height}px` }}>
      <Mark size="var(--h)" />
      <Wordmark variant="stacked" height={height} className={styles.word} />
    </span>
  )
}
