import { safetyContacts } from '@/lib/contacts'
import { Copy } from './Copy'
import styles from './SafetyFooter.module.css'

// On every plan screen (decision 10): "In danger? Call 911. To reach MARTA Police directly, call 404-848-4911 or
// text 1-877-371-8477." Words from copy.ts, numbers and links from the bundled contacts.json, so it renders with
// no server and no number is ever typed into copy.
export function SafetyFooter({ className }: { className?: string }) {
  const c = safetyContacts()
  const link = (x: { value: string; href: string | null }) => (x.href ? <a href={x.href}>{x.value}</a> : x.value)
  return (
    <aside className={`${styles.footer} ${className ?? ''}`} aria-label="Safety">
      <svg viewBox="0 0 24 24" width="22" height="22" className={styles.icon} aria-hidden="true" focusable="false">
        <path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinejoin="round" />
      </svg>
      <p>
        <Copy
          id="safety_footer"
          slots={{ emergency_911: link(c.emergency_911), police_emergency: link(c.police_emergency), police_text: link(c.police_text) }}
        />
      </p>
    </aside>
  )
}
