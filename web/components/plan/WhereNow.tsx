'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { lineColor } from '@/components/ui/LineBadge'
import { keepFocusAt } from '@/components/ui/Screen'
import { Skeleton } from '@/components/ui/Spinner'
import type { Line, NearbyTrain, Place, VehicleKey } from '@/contracts/api'
import { fixtureData, placesNearby, placesSearch, runtime, trainsNearby } from '@/lib/api'
import { t } from '@/lib/copy'
import { formatDistance } from '@/lib/distance'
import { getFix } from '@/lib/hooks'
import type { PlanAnswers, WhereNow as Where } from './planStore'
import { Answered, QuickChoice } from './QuickChoice'
import { NoBreak } from './FactText'
import { lineDirections, trainChoices } from './planModel'
import styles from './plan.module.css'

// "Where are you now?" for a train match (CHANGES 1), asked on the plan, never up front: At a station (pick it), On
// another train (decision 9's "Which train are you on now?" from /api/trains/nearby), or Somewhere else (the stop
// nearest a one-off fix, if location is allowed). Each answer re-requests the plan with the matching `user` block.
// A GPS fix is read fresh for the one lookup that needs it (places/nearby or trains/nearby) and never kept; a
// recorded set (?mode=offline or mock) answers the same anywhere, so there it is never asked for.
//
// Every question here sits inside a plan card whose title is an h2 ("Can you meet it?", "Meet it"), so the
// questions are h3. A tap that swaps the question for a picker, or a picker for its answer, keeps focus on the
// new content (keepFocusAt), never on <body>.

const lineWord = (l: Line) => t(`wb_08_train.${l}`)

export function WhereNow({
  answers,
  update,
  line,
  bagKeys,
  onElsewhere,
  noLocation,
}: {
  answers: PlanAnswers
  update: (patch: Partial<PlanAnswers>) => void
  line: Line | null
  bagKeys: readonly VehicleKey[]
  /** look up the stop nearest the rider for "Somewhere else" (the screen keeps it in memory only) */
  onElsewhere: () => void
  /** "Somewhere else" without a usable location: the plan can't time a meet */
  noLocation: boolean
}) {
  const [open, setOpen] = useState<'station' | 'train' | null>(null)
  const set = (where: Where | undefined, patch: Partial<PlanAnswers> = {}) => {
    setOpen(null)
    update({ where, station: undefined, train: undefined, ...patch })
  }

  if (open === 'station')
    return (
      <section className={styles.ask} aria-label={t('wb_02_now')}>
        <StationPicker onPick={(p) => set('station', { station: p })} onNotSure={() => set('unsure')} onBack={() => setOpen(null)} />
      </section>
    )
  if (open === 'train')
    return (
      <section className={styles.ask} aria-label={t('on_train')}>
        <TrainPicker
          line={line}
          bagKeys={bagKeys}
          onPick={(tr) => set('train', { train: { vehicle_key: tr.vehicle_key, line: tr.line, destination: tr.destination_label } })}
          onNotSure={() => set('unsure')}
          onNotTrain={() => setOpen(null)}
        />
      </section>
    )

  if (answers.where) {
    const text =
      answers.where === 'station' && answers.station
        ? t('where.answer_station', { station: answers.station.label })
        : answers.where === 'train' && answers.train
          ? t('where.answer_train', { line: lineWord(answers.train.line), destination: answers.train.destination })
          : answers.where === 'elsewhere'
            ? t('where.answer_elsewhere')
            : `${t('wb_02_now')} ${t('not_sure')}`
    return (
      <section className={styles.askDone}>
        <Answered onChange={() => set(undefined)} changeLabel={t('where.change')}>
          {text}
        </Answered>
        {answers.where === 'elsewhere' && noLocation ? <p className={styles.note}>{t('where.no_location')}</p> : null}
      </section>
    )
  }

  return (
    <section className={styles.ask}>
      <QuickChoice<'station' | 'train' | 'elsewhere' | 'unsure'>
        question={t('wb_02_now')}
        hint={t('where.lead')}
        layout="stack"
        options={[
          { value: 'station', label: t('where.station'), icon: 'pin' },
          { value: 'train', label: t('where.train'), icon: 'train' },
          { value: 'elsewhere', label: t('where.elsewhere'), icon: 'walk' },
          { value: 'unsure', label: t('not_sure'), quiet: true },
        ]}
        onChoose={(v) => {
          if (v === 'elsewhere') {
            set('elsewhere')
            onElsewhere()
          } else if (v === 'unsure') set('unsure')
          else setOpen(v)
        }}
      />
    </section>
  )
}

// ---------------------------------------------------------------------------------------------- which train

function TrainPicker({
  line,
  bagKeys,
  onPick,
  onNotSure,
  onNotTrain,
}: {
  line: Line | null
  bagKeys: readonly VehicleKey[]
  onPick: (t: NearbyTrain) => void
  onNotSure: () => void
  onNotTrain: () => void
}) {
  const [trains, setTrains] = useState<NearbyTrain[] | null>(null)
  const titleId = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => keepFocusAt(heading.current), [])
  const bag = bagKeys.join(',') // a stable dependency: the parent may pass a new array each render
  const recorded = fixtureData()

  useEffect(() => {
    if (!line) {
      setTrains([])
      return
    }
    const ctrl = new AbortController()
    const keys = bag.split(',')
    ;(async () => {
      // The candidate has no compass direction, so both of the line's directions are read to find the one the bag
      // train runs in, without the fix. Only that direction is asked again with the fix (for the distances and the
      // preselect): the rider's position goes in one request, never two. A recorded set never asks for it.
      const lists = await Promise.allSettled(lineDirections(line).map((direction) => trainsNearby({ line, direction }, { signal: ctrl.signal })))
      if (ctrl.signal.aborted) return
      const ok = lists.map((r) => (r.status === 'fulfilled' ? r.value.data.trains : null))
      const at = ok.findIndex((l) => l?.some((x) => keys.includes(x.vehicle_key)))
      if (at >= 0 && !recorded) {
        const fix = await getFix(5000) // used in this one request, never kept
        if (ctrl.signal.aborted) return
        if (fix) {
          try {
            const r = await trainsNearby({ line, direction: lineDirections(line)[at]!, ...fix }, { signal: ctrl.signal })
            if (ctrl.signal.aborted) return
            ok[at] = r.data.trains
          } catch {
            // the list without the fix still stands
          }
        }
      }
      if (ctrl.signal.aborted) return
      setTrains(trainChoices(ok.filter((l): l is NearbyTrain[] => l !== null), keys))
    })()
    return () => ctrl.abort()
  }, [line, bag, recorded])

  return (
    <div className={styles.picker}>
      <h3 id={titleId} ref={heading} tabIndex={-1} className={styles.quickQuestion}>
        {t('on_train')}
      </h3>
      {recorded ? null : <p className={styles.quickHint}>{t('on_train.location_line')}</p>}
      {trains === null ? (
        <Skeleton lines={3} height={56} />
      ) : trains.length === 0 ? (
        <>
          <p>{t('on_train.none')}</p>
          <BigButton variant="quiet" size="md" onClick={onNotSure}>
            {t('not_sure')}
          </BigButton>
        </>
      ) : (
        <ChoiceList<VehicleKey>
          labelledBy={titleId}
          selected={recorded ? null : (trains.find((x) => x.preselect)?.vehicle_key ?? null)}
          choices={trains.map((x) => {
            // a recorded set's distances and preselect were measured from where it was recorded, not this rider
            const sub = [x.preselect && !recorded ? t('on_train.closest') : null, typeof x.distance_m === 'number' && !recorded ? t('wb_05_off.away', { distance: formatDistance(x.distance_m) }) : null]
              .filter(Boolean)
              .join(', ')
            return {
              value: x.vehicle_key,
              label: t('on_train.option', { line: lineWord(x.line), destination: x.destination_label, next_stop: x.next_station?.label ?? null }),
              sublabel: sub ? <NoBreak text={sub} /> : undefined,
              swatch: lineColor(x.line),
            }
          })}
          onChoose={(v) => {
            const pick = trains.find((x) => x.vehicle_key === v)
            if (pick) onPick(pick)
            else onNotSure()
          }}
        />
      )}
      <BigButton variant="quiet" size="md" onClick={onNotTrain}>
        {t('on_train.not_train')}
      </BigButton>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- which station

function StationPicker({ onPick, onNotSure, onBack }: { onPick: (p: { place_id: string; label: string }) => void; onNotSure: () => void; onBack: () => void }) {
  const ids = useId()
  const [near, setNear] = useState<Place[] | null>(null)
  const [q, setQ] = useState('')
  const [found, setFound] = useState<Place[] | null>(null)
  const [searching, setSearching] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => keepFocusAt(heading.current), [])
  // a recorded set's distances were measured from where it was recorded, not from this rider: not shown
  const recorded = fixtureData()

  // nearest stations from one fix (or ?near= on stage, or the recorded set's own list); with location off the
  // search comes first
  useEffect(() => {
    const ctrl = new AbortController()
    ;(async () => {
      const noFix = Boolean(runtime().near) || fixtureData()
      const fix = noFix ? null : await getFix(5000)
      if (!fix && !noFix) {
        if (!ctrl.signal.aborted) setNear([])
        return
      }
      try {
        const { data } = await placesNearby({ lat: fix?.lat, lon: fix?.lon, mode: 'rail' }, { signal: ctrl.signal })
        if (!ctrl.signal.aborted) setNear(data.places.filter((p) => p.lines.length > 0 || p.kind === 'station'))
      } catch {
        if (!ctrl.signal.aborted) setNear([])
      }
    })()
    return () => ctrl.abort()
  }, [])

  const search = async () => {
    if (!q.trim()) return
    setSearching(true)
    try {
      const { data } = await placesSearch({ q: q.trim(), mode: 'rail' })
      const all = [data.resolved, ...data.choices].filter((p): p is Place => p !== null)
      setFound(all.filter((p, i) => all.findIndex((x) => x.place_id === p.place_id) === i))
    } catch {
      setFound([])
    } finally {
      setSearching(false)
    }
  }

  const list = (places: Place[]) => (
    <ChoiceList<string>
      label={t('where.station_title')}
      notSure={false}
      choices={places.map((p) => ({
        value: p.place_id,
        label: p.label,
        sublabel: typeof p.distance_m === 'number' && !recorded ? <NoBreak text={t('wb_05_off.away', { distance: formatDistance(p.distance_m) })} /> : undefined,
        icon: 'train',
      }))}
      onChoose={(v) => {
        const p = places.find((x) => x.place_id === v)
        if (p) onPick({ place_id: p.place_id, label: p.label })
      }}
    />
  )

  return (
    <div className={styles.picker}>
      <h3 ref={heading} tabIndex={-1} className={styles.quickQuestion}>
        {t('where.station_title')}
      </h3>
      {near === null ? (
        <>
          {recorded ? null : <p className={styles.quickHint}>{t('where.location_line')}</p>}
          <Skeleton lines={2} height={56} />
        </>
      ) : near.length ? (
        <>
          {recorded ? null : <p className={styles.quickHint}>{t('where.location_line')}</p>}
          {list(near)}
        </>
      ) : null}

      <form
        className={styles.inline}
        role="search"
        onSubmit={(e) => {
          e.preventDefault()
          void search()
        }}
      >
        <label htmlFor={`${ids}-q`} className={styles.inputLabel}>
          {t('where.station_search_label')}
        </label>
        <div className={styles.inputRow}>
          <input
            id={`${ids}-q`}
            className={styles.input}
            type="search"
            enterKeyHint="search"
            autoComplete="off"
            placeholder={t('where.station_hint')}
            value={q}
            onChange={(e) => setQ(e.currentTarget.value)}
          />
          <BigButton type="submit" variant="secondary" size="md" fullWidth={false} icon="search" loading={searching}>
            {t('where.station_search')}
          </BigButton>
        </div>
      </form>
      {found ? found.length ? list(found) : <p className={styles.note}>{t('where.station_none')}</p> : null}

      <BigButton variant="quiet" size="md" onClick={onNotSure}>
        {t('not_sure')}
      </BigButton>
      <button type="button" className={styles.linkButton} onClick={onBack}>
        {t('back')}
      </button>
    </div>
  )
}
