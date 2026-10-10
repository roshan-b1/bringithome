import { safetyContacts } from '@/lib/contacts'
import { t } from '@/lib/copy'
import { BigButton } from './BigButton'
import { Copy } from './Copy'
import styles from './SafetyFooter.module.css'

// On every plan screen (decision 10): "In danger? Call 911. To reach MARTA Police directly, call 404-848-4911 or
// text 1-877-371-8477." Words from copy.ts, numbers and links from the bundled contacts.json, so it renders with
// no server and no number is ever typed into copy.
//
// The sentence is for reading; the taps are real 56 px buttons under it. Inline links in a sentence were 40 px
// tall and "911" 27 px wide: the emergency route was the hardest target on the page for one stressed thumb. The
// numbers in the sentence stay plain text, so a screen reader meets each target once.
export function SafetyFooter({ className }: { className?: string }) {
  const c = safetyContacts()
  const num = (x: { value: string }) => <strong className={styles.number}>{x.value}</strong>
  return (
    <aside className={`${styles.footer} ${className ?? ''}`} aria-label="Safety">
      <div className={styles.say}>
        <svg viewBox="0 0 24 24" width="22" height="22" className={styles.icon} aria-hidden="true" focusable="false">
          <path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinejoin="round" />
        </svg>
        <p>
          <Copy id="safety_footer" slots={{ emergency_911: num(c.emergency_911), police_emergency: num(c.police_emergency), police_text: num(c.police_text) }} />
        </p>
      </div>
      <div className={styles.buttons}>
        {c.emergency_911.href ? (
          <BigButton variant="danger" size="md" icon="phone" href={c.emergency_911.href} className={styles.button}>
            {t('action.call_911', { number: c.emergency_911.value })}
          </BigButton>
        ) : null}
        {c.police_emergency.href ? (
          <BigButton variant="secondary" size="md" icon="phone" href={c.police_emergency.href} className={styles.button}>
            {t('safety.call_police')}
          </BigButton>
        ) : null}
        {c.police_text.href ? (
          <BigButton variant="secondary" size="md" icon="message" href={c.police_text.href} className={styles.button}>
            {t('safety.text_police')}
          </BigButton>
        ) : null}
      </div>
    </aside>
  )
}
