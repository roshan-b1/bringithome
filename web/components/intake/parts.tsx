'use client'

import type { ReactNode } from 'react'
import type { Place } from '@/contracts/api'
import { ReplayBanner } from '@/components/ui/Banner'
import { BigButton } from '@/components/ui/BigButton'
import { useSource } from '@/components/ui/Chip'
import { Icon, type IconName } from '@/components/ui/Icon'
import { lineColor, lineName } from '@/components/ui/LineBadge'
import { t } from '@/lib/copy'
import { formatDistance } from '@/lib/distance'
import type { PlaceTap } from '@/lib/flow'
import type { FlowSession } from '@/lib/useFlow'
import styles from './intake.module.css'

// Small pieces the intake screens share. Nothing here fetches or stores anything.

export interface IntakeProps {
  flow: FlowSession
}

/** Back, when there is somewhere to go back to. */
export const backOf = (flow: FlowSession): (() => void) | null => (flow.state.history.length ? flow.back : null)

/**
 * "Replaying Fri Oct 9, 1:24 PM" whenever the latest answer came from recorded data (decision 13), on the screens
 * whose content depends on the clock or the data: in a replay, "10 to 30 min ago" means before the replayed moment.
 */
export function IntakeBanner() {
  const s = useSource()
  if (!s || s.source === 'live' || !s.asOf) return null
  return <ReplayBanner asOf={s.asOf} withHint={false} />
}

/** "Not sure", placed by the screen itself (ChoiceList's own sits right under its rows). */
export function NotSureButton({ onClick, pressed, label }: { onClick: () => void; pressed?: boolean; label?: string }) {
  return (
    <button type="button" className={styles.notSure} aria-pressed={pressed ?? false} onClick={onClick}>
      {label ?? t('not_sure')}
    </button>
  )
}

/** "Type it instead" (TellMe) as a plain button (the done screen's way back in). */
export function TypeInstead({ flow }: IntakeProps) {
  return (
    <BigButton variant="secondary" size="md" icon="mic" onClick={() => flow.goTo('tell')}>
      {t('intake.type_instead')}
    </BigButton>
  )
}

/**
 * The front door to TellMe at the top of "Where did you get off?": one card with a mic, clearly tappable but quieter
 * than the nearby list under it (outlined, no fill), with an example sentence so it reads at a glance.
 */
export function SayItCard({ flow }: IntakeProps) {
  return (
    <button type="button" className={styles.sayCard} onClick={() => flow.goTo('tell')}>
      <span className={styles.sayIcon} aria-hidden="true">
        <Icon name="mic" size={24} />
      </span>
      <span className={styles.sayText}>
        <span className={styles.sayTitle}>{t('off.say_title')}</span>
        <span className={styles.sayExample}>{t('off.say_example')}</span>
      </span>
      <Icon name="next" size={20} className={styles.sayNext} />
    </button>
  )
}

/** "Lost it somewhere else?": a small link to the somewhere-else path, under everything else on off. */
export function SomewhereElse({ flow }: IntakeProps) {
  return (
    <button type="button" className={styles.elseLink} onClick={flow.notOnMarta}>
      <Icon name="home" size={20} />
      <span>{t('off.somewhere_else')}</span>
    </button>
  )
}

/** A one-line calm status with an icon. */
export function Note({ icon = 'alert', warm = false, children, id }: { icon?: IconName; warm?: boolean; children: ReactNode; id?: string }) {
  return (
    <p className={`${styles.note} ${warm ? styles.noteWarm : ''}`} id={id}>
      <Icon name={icon} size={20} className={styles.noteIcon} />
      <span>{children}</span>
    </p>
  )
}

/** "170 ft away" or "1.2 mi away" (the place row keeps it on one line). */
export function distanceText(m: number): string {
  return t('wb_05_off.away', { distance: formatDistance(m) })
}

// The Atlanta Streetcar's route in the schedule feed: shown as "Streetcar", never as its feed id (flow.ts reads it too)
const STREETCAR_ROUTE = 'ATLSC'

/** "Route 49", "Routes 49, 55", "Routes 3, 10, 20, 42 and 2 more"; the streetcar by name. */
export function routesText(all: readonly string[]): string | null {
  const routes = all.filter((r) => r !== STREETCAR_ROUTE)
  const car = routes.length < all.length ? t('wb_04_mode.streetcar') : null
  let text: string | null = null
  if (routes.length === 1) text = t('wb_08_bus.route', { route: routes[0]! })
  else if (routes.length > 1 && routes.length <= 4) text = t('off.routes', { routes: routes.join(', ') })
  else if (routes.length > 4) text = t('off.routes_more', { routes: routes.slice(0, 4).join(', '), more: routes.length - 4 })
  return [text, car].filter(Boolean).join(', ') || null
}

/**
 * A place's second line: how far (only right after the GPS lookup; never stored), then its routes, and its rail
 * lines below in two columns, each colour beside its name. Distance and routes share a line when they fit, with a
 * dot between them drawn by CSS; when the routes move to their own line the dot is clipped, so no line starts or
 * ends with a stray dot.
 */
export function PlaceSub({ place, distance }: { place: Place | PlaceTap; distance?: number | null }) {
  const routes = routesText(place.routes)
  const dist = distance !== undefined && distance !== null ? distanceText(distance) : null
  return (
    <span className={styles.placeSub}>
      {dist || routes ? (
        <span className={styles.meta}>
          {dist ? <span className={styles.dist}>{dist}</span> : null}
          {dist && routes ? <span className="sr-only">, </span> : null}
          {routes ? <span className={styles.routes}>{routes}</span> : null}
        </span>
      ) : null}
      {place.lines.length ? (
        <span className={styles.lines}>
          {place.lines.map((l) => (
            <span key={l} className={styles.line}>
              <span className={styles.lineDot} style={{ background: lineColor(l) }} aria-hidden="true" />
              {lineName(l)}
            </span>
          ))}
        </span>
      ) : null}
    </span>
  )
}

/** The icon for a kind of place. */
export const placeIcon = (p: Place | PlaceTap): IconName => (p.kind === 'station' || (p.lines.length > 0 && p.routes.length === 0) ? 'train' : p.kind === 'alias' ? 'pin' : 'bus')

/** "Something else": three dots, drawn like the shared icon set (one rounded stroke). */
export function MoreGlyph({ size = 24 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={3.5} strokeLinecap="round" aria-hidden="true" focusable="false">
      <path d="M5.5 12h.01M12 12h.01M18.5 12h.01" />
    </svg>
  )
}

/** "the store" -> "The store", for a list row (the call line keeps "Call the store."). */
export const capitalize = (s: string): string => (s ? s[0]!.toUpperCase() + s.slice(1) : s)
