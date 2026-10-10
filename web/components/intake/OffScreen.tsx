'use client'

import type { Place } from '@/contracts/api'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { Screen } from '@/components/ui/Screen'
import { fixtureData } from '@/lib/api'
import { t } from '@/lib/copy'
import { isPlace, NOT_SURE, placeTap } from '@/lib/flow'
import { backOf, IntakeBanner, Note, NotSureButton, PlaceSub, placeIcon, SayItCard, SomewhereElse, type IntakeProps } from './parts'
import { PlaceSearch } from './PlaceSearch'
import { useNearby } from './useNearby'
import styles from './intake.module.css'

// Question 1: where did you get off? "Say it in one sentence" (TellMe) on top, then the nearest stops and stations
// to where the rider is now (most notice soon after getting off), each with its bus or train icon and its routes or
// lines, then a search, "Not sure" and "Lost it somewhere else?". Both kinds are listed (mode "any"): the place
// tapped says bus or train (flow.inferRide), so that question is asked only when the place can't. With location
// denied or unavailable, the search comes first and nothing else waits on GPS.

const mode = 'any' as const

export function OffScreen({ flow }: IntakeProps) {
  const a = flow.state.answers
  const { state, retry } = useNearby(mode)
  const chosen = isPlace(a.off) ? a.off : null
  const pick = (p: Place) => flow.answer('off', placeTap(p))

  const nearby = state.kind === 'ready' ? state.places : []
  // A recorded set's places-nearby was measured from where it was recorded (the set's own ?near), not from this
  // rider: shown as "Near you now" with "50 ft away" it would present a recorded fact as their own distance. So a
  // recorded list says where it comes from and shows no distances.
  const recorded = fixtureData()
  const nearHeading = recorded ? t('off.recorded_heading') : t('off.near_heading')
  const nearFound = recorded
    ? nearby.length === 1 ? t('off.recorded_found_one') : t('off.recorded_found', { count: nearby.length })
    : nearby.length === 1 ? t('off.near_found_one') : t('off.near_found', { count: nearby.length })
  // a stop chosen earlier (by search or TellMe) that isn't in today's nearest list still shows, pressed, on Back
  const showChosen = chosen && !nearby.some((p) => p.place_id === chosen.place_id)
  const searchFirst = state.kind === 'no_fix' || state.kind === 'failed' || (state.kind === 'ready' && nearby.length === 0)

  const search = (
    <PlaceSearch mode={mode} route={a.route} onPick={pick} selectedId={chosen?.place_id ?? null} autoFocus={false} />
  )

  return (
    <Screen
      stepKey="off"
      title={t('wb_05_off')}
      progress={flow.progress}
      onBack={backOf(flow)}
      chip={<SourceChip />}
      banner={<IntakeBanner />}
    >
      <SayItCard flow={flow} />

      {showChosen ? (
        <section className={styles.section} aria-label={t('off.your_answer')}>
          <h2 className={styles.sectionHeading}>{t('off.your_answer')}</h2>
          <ChoiceList
            label={t('off.your_answer')}
            notSure={false}
            selected={chosen.place_id}
            onChoose={() => flow.answer('off', chosen)}
            choices={[{ value: chosen.place_id, label: chosen.label, sublabel: <PlaceSub place={chosen} />, icon: placeIcon(chosen) }]}
          />
        </section>
      ) : null}

      {state.kind === 'asking' || state.kind === 'loading' ? (
        <section className={styles.section} aria-busy="true">
          <div role="status">
            <Note icon="locate">{t('off.locating')}</Note>
          </div>
          <div className={styles.skeletonRows} aria-hidden="true">
            <span className={styles.skeletonRow} />
            <span className={styles.skeletonRow} />
            <span className={styles.skeletonRow} />
          </div>
        </section>
      ) : null}

      {state.kind === 'no_fix' ? (
        <>
          <Note icon="locate" warm>
            {state.blocked ? t('wb_02_now.location_denied') : t('off.location_unavailable')}
          </Note>
          {search}
          {state.blocked ? null : (
            <BigButton variant="secondary" size="md" icon="locate" onClick={retry}>
              {t('off.use_location')}
            </BigButton>
          )}
        </>
      ) : null}

      {state.kind === 'failed' ? (
        <>
          <Note warm>{t('off.near_failed')}</Note>
          {search}
        </>
      ) : null}

      {state.kind === 'ready' && nearby.length === 0 ? (
        <>
          <Note icon="search" warm>
            {t('off.near_none')}
          </Note>
          {search}
        </>
      ) : null}

      {state.kind === 'ready' && nearby.length > 0 ? (
        <section className={styles.section} aria-label={nearHeading}>
          <h2 className={styles.sectionHeading}>{nearHeading}</h2>
          <ChoiceList
            label={nearHeading}
            notSure={false}
            selected={chosen?.place_id ?? null}
            onChoose={(v) => {
              const p = nearby.find((x) => x.place_id === v)
              if (p) pick(p)
            }}
            choices={nearby.map((p) => ({
              value: p.place_id,
              label: p.label,
              sublabel: <PlaceSub place={p} distance={recorded ? null : p.distance_m} />,
              icon: placeIcon(p),
            }))}
          />
          <LiveRegion text={nearFound} />
        </section>
      ) : null}

      {/* with location working (or still being asked) the search sits under the nearest stops, never waiting on GPS */}
      {searchFirst ? null : search}

      <NotSureButton pressed={a.off === NOT_SURE} onClick={() => flow.answer('off', NOT_SURE)} />
      <SomewhereElse flow={flow} />
    </Screen>
  )
}
