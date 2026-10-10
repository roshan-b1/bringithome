'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { Icon } from '@/components/ui/Icon'
import { lineColor, lineName } from '@/components/ui/LineBadge'
import { Screen } from '@/components/ui/Screen'
import { serverNow } from '@/lib/api'
import { t } from '@/lib/copy'
import { NOT_SURE, rideChips, type TimeButton } from '@/lib/flow'
import { clock, nyParts, resolvePickedTime, sameNyDay } from '@/lib/time'
import { backOf, IntakeBanner, NotSureButton, type IntakeProps } from './parts'
import styles from './intake.module.css'

// The last question: about when did you get off? "Just now" (the under-10-minutes button) first and biggest, since
// most riders notice soon after, then the other time buttons (decision 6: each resolves against the server clock
// when the intake is built, so a phone set to the wrong time can't skew the match), "Pick a time" with the phone's
// own time picker read as New York time, and "Not sure". The last tap before the match.
// The smaller buttons sit in two columns, so everything fits on an iPhone SE without scrolling, in time order, with
// "Not sure" in the last cell.
//
// Above them, only when the place has more than one: "Know your bus? Tap it." with the stop's routes, or "Know your
// line? Tap it." with the station's lines (flow.rideChips, following the mode). Optional and quiet (outlined, no
// fill), so "Just now" stays the obvious tap; it sits first because a time tap moves on at once. One tap picks a
// route or line (Intake.route or Intake.line), a second tap clears it.

const BUTTONS: readonly TimeButton[] = ['10_30', '30_60', '1_2h', 'over_2h']
type Choice = TimeButton | 'pick'

/** Now on the New York wall clock as "HH:MM", rounded down to 5 minutes (what the picker opens on). */
function nyHhmm(d: Date): string {
  const p = nyParts(d)
  return `${String(p.hour).padStart(2, '0')}:${String(p.minute - (p.minute % 5)).padStart(2, '0')}`
}

export function WhenScreen({ flow }: IntakeProps) {
  const when = flow.state.answers.when
  const [picking, setPicking] = useState(typeof when === 'object' && when.kind === 'picked')
  const [now, setNow] = useState<Date | null>(null)
  const [hhmm, setHhmm] = useState<string>(typeof when === 'object' && when.kind === 'picked' ? when.hhmm : '')
  const panel = useRef<HTMLDivElement>(null)
  const inputId = useId()

  // the server (or replay) clock: the picker opens on its time, and "last night" is judged against it
  useEffect(() => {
    let live = true
    serverNow().then((d) => {
      if (!live) return
      setNow(d)
      setHhmm((v) => v || nyHhmm(d))
    })
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    if (!picking) return
    panel.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    panel.current?.querySelector('input')?.focus({ preventScroll: true })
  }, [picking])

  const selected: Choice | 'not_sure' | null =
    when === undefined ? null : when === NOT_SURE ? NOT_SURE : when.kind === 'relative' ? when.button : 'pick'

  let at: Date | null = null
  try {
    at = now && hhmm ? resolvePickedTime(hhmm, now) : null
  } catch {
    at = null // a half-typed time on a desktop browser
  }
  const yesterday = at && now && !sameNyDay(at, now)
  const night = at ? nyParts(at).hour >= 18 || nyParts(at).hour < 5 : false
  const day = at ? nyParts(at).weekday : ''

  return (
    <Screen stepKey="when" title={t('wb_06_when')} progress={flow.progress} onBack={backOf(flow)} chip={<SourceChip />} banner={<IntakeBanner />}>
      <RideChipRow flow={flow} />
      <button
        type="button"
        className={styles.nowTile}
        aria-pressed={!picking && selected === 'under_10'}
        onClick={() => flow.answer('when', { kind: 'relative', button: 'under_10' })}
      >
        <Icon name="clock" size={28} />
        <span className={styles.nowText}>
          <span className={styles.nowLabel}>{t('when.just_now')}</span>
          <span className={styles.nowHint}>{t('when.just_now_hint')}</span>
        </span>
        <Icon name="next" size={24} />
      </button>
      <ul role="list" className={`${styles.timeGrid} ${styles.timeGridSmall}`} aria-label={t('when.earlier')}>
        {BUTTONS.map((b) => (
          <li key={b}>
            <button
              type="button"
              className={styles.timeTile}
              aria-pressed={!picking && selected === b}
              onClick={() => flow.answer('when', { kind: 'relative', button: b })}
            >
              <span className={styles.timeLabel}>{t(`wb_06_when.${b}`)}</span>
              <Icon name="check" size={20} strokeWidth={3} className={styles.timeCheck} />
            </button>
          </li>
        ))}
        <li>
          <button
            type="button"
            className={`${styles.timeTile} ${styles.timePick}`}
            aria-pressed={picking}
            aria-expanded={picking}
            aria-controls={`${inputId}-panel`}
            onClick={() => setPicking((p) => !p)}
          >
            <Icon name="clock" size={22} className={styles.timePickIcon} />
            <span className={styles.timeLabel}>{t('wb_06_when.pick')}</span>
          </button>
        </li>
        {/* "Not sure" fills the grid's last cell beside "Pick a time", so the optional chips above still leave
            every choice on an iPhone SE screen */}
        <li>
          <NotSureButton pressed={!picking && selected === NOT_SURE} onClick={() => flow.answer('when', NOT_SURE)} />
        </li>
      </ul>

      {picking ? (
        <div className={styles.pickPanel} ref={panel} id={`${inputId}-panel`}>
          <label htmlFor={inputId} className={styles.label}>
            {t('wb_06_when.pick_label')}
          </label>
          <input
            id={inputId}
            type="time"
            step={300}
            className={styles.timeInput}
            value={hhmm}
            onChange={(e) => setHhmm(e.target.value)}
            aria-describedby={yesterday ? `${inputId}-day` : undefined}
          />
          {yesterday && at ? (
            <p id={`${inputId}-day`} className={styles.yesterday} aria-live="polite">
              {night ? t('wb_06_when.picked_yesterday', { time: clock(at), day }) : t('when.picked_yesterday_day', { time: clock(at), day })}
            </p>
          ) : null}
          <BigButton variant="primary" iconEnd="next" disabled={!at} onClick={() => flow.answer('when', { kind: 'picked', hhmm })}>
            {at ? t('when.use_time', { time: clock(at) }) : t('continue')}
          </BigButton>
        </div>
      ) : null}
    </Screen>
  )
}

/** "Know your bus? Tap it." / "Know your line? Tap it.": one row of chips, scrolling sideways when a stop has many. */
function RideChipRow({ flow }: IntakeProps) {
  const labelId = useId()
  const chips = rideChips(flow.state.answers)
  if (!chips) return null
  const label = chips.kind === 'route' ? t('when.know_bus') : t('when.know_line')
  return (
    <div className={styles.rideRow} role="group" aria-labelledby={labelId}>
      <p id={labelId} className={styles.rideLabel}>
        {label}
      </p>
      <ul role="list" className={styles.rideChips}>
        {chips.kind === 'route'
          ? chips.values.map((r) => (
              <li key={r}>
                <button
                  type="button"
                  className={styles.rideChip}
                  aria-pressed={chips.selected === r}
                  aria-label={t('wb_08_bus.route', { route: r })}
                  onClick={() => flow.setExtra({ route: chips.selected === r ? null : r })}
                >
                  {r}
                  <Icon name="check" size={18} strokeWidth={3} className={styles.rideCheck} />
                </button>
              </li>
            ))
          : chips.values.map((l) => (
              <li key={l}>
                <button
                  type="button"
                  className={styles.rideChip}
                  aria-pressed={chips.selected === l}
                  aria-label={lineName(l)}
                  onClick={() => flow.setExtra({ line: chips.selected === l ? null : l })}
                >
                  <span className={styles.rideSwatch} style={{ background: lineColor(l) }} aria-hidden="true" />
                  {lineName(l, false)}
                  <Icon name="check" size={18} strokeWidth={3} className={styles.rideCheck} />
                </button>
              </li>
            ))}
      </ul>
    </div>
  )
}
