'use client'

import { useId, useState, type ReactNode } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { Icon, type IconName } from '@/components/ui/Icon'
import { lineName } from '@/components/ui/LineBadge'
import { Screen } from '@/components/ui/Screen'
import { t } from '@/lib/copy'
import { placeTap, type Proposal, type TellTap } from '@/lib/flow'
import { clock } from '@/lib/time'
import { backOf, IntakeBanner, PlaceSub, placeIcon, type IntakeProps } from './parts'
import styles from './intake.module.css'

// TellMe's second half: what /api/parse read, shown back for the rider to confirm by tap (decision 11: places and
// times come back as resolver choices the rider still confirms). Nothing reaches the flow until "Yes, that's
// right". Each fact is a big row the rider can leave out; several places mean a pick is required. Whatever is left
// out is asked by tap afterwards, so a wrong read never costs more than the taps it would have taken anyway.

const RIDE_ICON: Record<NonNullable<Proposal['mode']>, IconName> = { bus: 'bus', rail: 'train', streetcar: 'streetcar' }
const ITEM_ICON: Record<NonNullable<Proposal['item']>, IconName> = { phone: 'phone', wallet: 'wallet', bag: 'bag', keys: 'key', laptop: 'laptop', other: 'bag' }

export function TellConfirm({ flow, proposal: p, onAgain }: IntakeProps & { proposal: Proposal; onAgain: () => void }) {
  const choices = p.anchor?.choices ?? []
  const single = choices.length === 1
  const [keepItem, setKeepItem] = useState(true)
  const [keepRide, setKeepRide] = useState(true)
  const [keepWhen, setKeepWhen] = useState(true)
  const [placeId, setPlaceId] = useState<string | null>(single ? choices[0]!.place_id : null) // 'none' = none of these
  const group = useId()

  const ride = rideLabel(p)
  const placeChosen = choices.find((c) => c.place_id === placeId) ?? null
  const needsPick = choices.length > 1 && placeId === null

  const yes = () => {
    const tap: TellTap = {}
    if (p.item && keepItem) tap.item = p.item
    if (ride && keepRide) {
      if (p.mode) tap.mode = p.mode
      if (p.route) tap.route = p.route
      if (p.line) tap.line = p.line
    }
    if (placeChosen && p.anchor) {
      tap.place = placeTap(placeChosen)
      tap.role = p.anchor.role
    }
    // a boarding time without its boarding place would be read as the time they got off: leave it out
    const timeFits = p.anchor?.role !== 'board' || placeChosen !== null
    if (p.when && keepWhen && timeFits) tap.when = { at: p.when.at, window_min: p.when.window_min, picked: p.when.picked }
    flow.answer('tell', tap)
  }

  return (
    <Screen
      stepKey="tell-confirm"
      title={t('tell.confirm_title')}
      lead={t('tell.confirm_lead')}
      onBack={backOf(flow)}
      chip={<SourceChip />}
      banner={<IntakeBanner />}
      actions={
        <>
          <BigButton variant="primary" icon="check" disabled={needsPick} onClick={yes}>
            {t('tell.yes')}
          </BigButton>
          <BigButton variant="secondary" size="md" onClick={onAgain}>
            {t('tell.again')}
          </BigButton>
        </>
      }
    >
      <div className={styles.facts}>
        {p.item ? (
          <div className={styles.fact}>
            <p className={styles.factEyebrow}>{t('tell.what')}</p>
            <Check checked={keepItem} onChange={setKeepItem} icon={ITEM_ICON[p.item]} label={t(`wb_01_what.${p.item}`)} />
          </div>
        ) : null}

        {ride ? (
          <div className={styles.fact}>
            <p className={styles.factEyebrow}>{t('tell.ride')}</p>
            <Check checked={keepRide} onChange={setKeepRide} icon={p.mode ? RIDE_ICON[p.mode] : 'bus'} label={ride} />
          </div>
        ) : null}

        {p.anchor && choices.length ? (
          <div className={styles.fact} role={single ? undefined : 'radiogroup'} aria-labelledby={`${group}-h`}>
            <p className={styles.factEyebrow} id={`${group}-h`}>
              {p.anchor.role === 'board' ? t('tell.where_on') : t('tell.where_off')}
            </p>
            {p.anchor.phrase ? <p className={styles.said}>{t('tell.you_said', { phrase: p.anchor.phrase })}</p> : null}
            {single ? (
              <Check
                checked={placeId !== null && placeId !== 'none'}
                onChange={(on) => setPlaceId(on ? choices[0]!.place_id : 'none')}
                icon={placeIcon(choices[0]!)}
                label={choices[0]!.label}
                sub={<PlaceSub place={choices[0]!} />}
              />
            ) : (
              <>
                <p className={styles.hint}>{t('tell.pick_one')}</p>
                {choices.map((c) => (
                  <Radio key={c.place_id} name={group} checked={placeId === c.place_id} onChange={() => setPlaceId(c.place_id)} icon={placeIcon(c)} label={c.label} sub={<PlaceSub place={c} />} />
                ))}
                <Radio name={group} checked={placeId === 'none'} onChange={() => setPlaceId('none')} icon="close" label={t('tell.none_of_these')} />
              </>
            )}
          </div>
        ) : null}

        {p.when ? (
          <div className={styles.fact}>
            <p className={styles.factEyebrow}>{t('tell.when')}</p>
            {p.when.phrase ? <p className={styles.said}>{t('tell.you_said', { phrase: p.when.phrase })}</p> : null}
            <Check
              checked={keepWhen}
              onChange={setKeepWhen}
              icon="clock"
              label={t('tell.time', { time: clock(p.when.at), day: p.when.day_label })}
            />
          </div>
        ) : null}

        <p className={styles.hint}>{t('tell.asked_next')}</p>
      </div>
    </Screen>
  )
}

/** "Bus, route 49", "Train, Red Line", "Streetcar"; or a route or line alone when the ride was already tapped. */
function rideLabel(p: Proposal): string | null {
  if (p.mode === 'bus') return t('tell.mode_bus', { route: p.route ?? null })
  if (p.mode === 'rail') return p.line ? `${t('tell.mode_rail')}, ${lineName(p.line)}` : t('tell.mode_rail')
  if (p.mode === 'streetcar') return t('tell.mode_streetcar')
  if (p.route) return t('wb_08_bus.route', { route: p.route })
  if (p.line) return lineName(p.line)
  return null
}

/** A fact the rider keeps (checked) or leaves out: a real checkbox, drawn as a 64 px row. */
function Check({ checked, onChange, icon, label, sub }: { checked: boolean; onChange: (v: boolean) => void; icon: IconName; label: string; sub?: ReactNode }) {
  return (
    <label className={styles.check}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className={styles.box} aria-hidden="true">
        <Icon name="check" size={18} strokeWidth={3} />
      </span>
      <Icon name={icon} className={styles.factIcon} />
      <span className={styles.checkText}>
        <span>{label}</span>
        {sub ? <span className={styles.checkSub}>{sub}</span> : null}
      </span>
    </label>
  )
}

/** One of several places: a real radio, drawn as a 64 px row. */
function Radio({ name, checked, onChange, icon, label, sub }: { name: string; checked: boolean; onChange: () => void; icon: IconName; label: string; sub?: ReactNode }) {
  return (
    <label className={`${styles.check} ${styles.radio}`}>
      <input type="radio" name={name} checked={checked} onChange={onChange} />
      <span className={styles.box} aria-hidden="true">
        <Icon name="check" size={18} strokeWidth={3} />
      </span>
      <Icon name={icon} className={styles.factIcon} />
      <span className={styles.checkText}>
        <span>{label}</span>
        {sub ? <span className={styles.checkSub}>{sub}</span> : null}
      </span>
    </label>
  )
}
