'use client'

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Mark } from '@/components/brand/Mark'
import { ReplayBanner } from '@/components/ui/Banner'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { ResumeError } from '@/components/intake/ClueReport'
import { ErrorState } from '@/components/ui/ErrorState'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { SafetyFooter } from '@/components/ui/SafetyFooter'
import { Screen } from '@/components/ui/Screen'
import { Skeleton, Spinner } from '@/components/ui/Spinner'
import type { IconName } from '@/components/ui/Icon'
import type { ScreenComponentProps } from '@/components/screens/registry'
import type { ItemCategory, PlanUser, ReunionKind, SessionResponse, VehicleKey } from '@/contracts/api'
import type { TargetPoint } from '@/components/results/mapEngine'
import { useRiderLocation } from '@/components/results/useRiderLocation'
import { fixtureData, openRecordedExample, placesNearby, runtime } from '@/lib/api'
import { contact, lostFoundForm } from '@/lib/contacts'
import { t, type CopyId, tLoose } from '@/lib/copy'
import { getFix, useVisibleInterval, useWide } from '@/lib/hooks'
import { clearNow as clearSession } from '@/lib/session'
import { clock } from '@/lib/time'
import type { FlowSession } from '@/lib/useFlow'
import { CallCard } from './CallCard'
import { ClearNow } from './ClearNow'
import { MeetCard, meetLines } from './MeetCard'
import { PlanAside } from './PlanAside'
import { OneCard, PlanCards, type CardOverride, type CardSlots } from './PlanCards'
import { ProofFooter } from './ProofFooter'
import { Answered, QuickChoice } from './QuickChoice'
import { ReportCard } from './ReportCard'
import { Ways } from './Reunion'
import { changeSaid, mainOption, reunionBody, walksThere, type PollFix } from './reunionModel'
import { WhereNow } from './WhereNow'
import { usePlaceCoords } from './placeCoords'
import {
  arrangeCards,
  bagLine,
  chosenKeys,
  etaAt,
  fact,
  fillItem,
  haversineM,
  isThere,
  keyMode,
  mailtoHref,
  minutesUntil,
  nowOf,
  planSections,
  remainingWalkS,
  topCanceled,
  walkMinutes,
  type Car,
  type CardContacts,
  type ReportLabels,
  type RiderSpot,
  type Taken,
  type Walk,
} from './planModel'
import { clearPlanAnswers, usePlanAnswers } from './planStore'
import { locationPermission, meetPosition } from './useRiderPosition'
import { useMeetWatch } from './useMeetWatch'
import { usePlanRequest, type PlanBody } from './usePlanRequest'
import { useReunion } from './useReunion'
import styles from './plan.module.css'

// The plan step (decision 10, CHANGES 1 and 2): POST /api/sessions/{id}/plan as soon as the screen opens, the
// cards as a route, and the few optional questions only where they change something: for a train "Where are you
// now?" (the meet), on a train meet "Which part of the train?" (the platform hint). Theft is not asked: a quiet
// "Think someone took it?" link at the end (TakenLink) sends taken "yes" (safety first, freeze cards) and its Undo
// sends "no"; never tapped, no taken is sent. Each answer re-requests the plan. While a meet shows, /now is polled with the meet check and the
// rider's walk comes from the live position. The safety footer is on every view; "Clear now" deletes everything.
//
// Mounted by the integrator as SCREENS.plan; it reads only the session, the session id, the item tap and the
// slow-walk toggle from the flow, plus `vehicleKeys`: the rider's pick on the results screen (Results' onContinue),
// until FlowSession carries it. Without it the plan names what the match names (planVehicleKeys).

type View = 'plan' | 'done' | 'cleared'

export interface PlanScreenProps extends ScreenComponentProps {
  /** 1 or 2 of the session's candidates the rider settled on in results; ignored unless the API would name them */
  vehicleKeys?: readonly VehicleKey[] | null
}

export function PlanScreen({ flow, vehicleKeys = null }: PlanScreenProps) {
  const [view, setView] = useState<View>('plan')
  const session = flow.session?.data ?? null

  if (view === 'cleared')
    return (
      <Screen
        stepKey="plan_cleared"
        title={t('cleared')}
        aside={false}
        footer={<SafetyFooter />}
        actions={
          <BigButton variant="primary" onClick={flow.restart}>
            {t('done')}
          </BigButton>
        }
      >
        <Mark size={88} className={styles.endMark} />
      </Screen>
    )
  if (!flow.ready)
    return (
      <Screen stepKey="plan" title={t('plan.title')} chip={<SourceChip />} footer={<SafetyFooter />}>
        <Spinner showLabel label={t('plan.loading')} />
      </Screen>
    )
  if (flow.resumeError || !session)
    return (
      <Screen stepKey="plan" title={flow.resumeError ? t('plan.error_title') : t('plan.title')} chip={<SourceChip />} footer={<SafetyFooter />}>
        {flow.resumeError ? (
          <ResumeError flow={flow} />
        ) : (
          <>
            <p>{t('plan.no_session')}</p>
            <BigButton variant="primary" onClick={flow.restart}>
              {t('plan.start_over')}
            </BigButton>
          </>
        )}
      </Screen>
    )
  return <PlanView flow={flow} session={session} view={view} setView={setView} pick={vehicleKeys} />
}

// ------------------------------------------------------------------------------------------------------ the view

const ITEMS: readonly ItemCategory[] = ['phone', 'wallet', 'bag', 'keys', 'laptop', 'other']

function cardContacts(): CardContacts | null {
  const police = contact('police_non_emergency')
  const e911 = contact('emergency_911')
  return police && e911 ? { police_non_emergency: police, emergency_911: e911 } : null
}

function reportLabels(): ReportLabels {
  const l = lostFoundForm().labels
  return {
    route_number: l.route_number ?? t('form.route_number'),
    bus_number: l.bus_number ?? t('form.bus_number'),
    direction: l.direction ?? t('form.direction'),
    location: t('form.location'),
    time: t('form.time'),
    rail_line: l.rail_line ?? t('form.rail_line'),
    rail_station: l.rail_station ?? t('form.rail_station'),
    rail_car_number: l.rail_car_number ?? t('form.rail_car_number'),
    note: t('report.note_label'),
  }
}

function PlanView({
  flow,
  session,
  view,
  setView,
  pick,
}: {
  flow: FlowSession
  session: SessionResponse
  view: View
  setView: (v: View) => void
  pick: readonly VehicleKey[] | null
}) {
  const sessionId = session.session_id
  const match = session.match
  const [answers, update, loaded] = usePlanAnswers(sessionId)
  // the pick from results wins; after a reload (the prop is gone) the stored tap stands in for it
  const pickKey = pick?.join(',') ?? ''
  const storedKey = answers.keys?.join(',') ?? ''
  const keysKey = chosenKeys(match, pickKey ? pickKey.split(',') : storedKey ? storedKey.split(',') : null).join(',')
  const keys = useMemo(() => (keysKey ? keysKey.split(',') : []), [keysKey])
  useEffect(() => {
    if (loaded && pickKey && pickKey !== storedKey) update({ keys: pickKey.split(',') })
  }, [loaded, pickKey, storedKey, update])
  const mode = keyMode(keys[0])
  const line = bagLine(match, keys)
  const tie = keys.length > 1

  // the flow's own taps this screen reads: the item (asked here, "What did you lose?": for [[ITEM]] and the item's own
  // steps, sent as item_category) and "I walk slowly" (decision 9)
  const flowAnswers = flow.state.answers as { what?: unknown; walk?: unknown }
  const tappedItem = ITEMS.includes(flowAnswers.what as ItemCategory) ? (flowAnswers.what as ItemCategory) : null
  const category = tappedItem ?? 'other'
  const itemAnswer: ItemCategory | 'not_sure' | undefined = tappedItem ?? (flowAnswers.what === 'not_sure' ? 'not_sure' : undefined)
  const flowWalk: Walk | null = flowAnswers.walk === 'slow' || flowAnswers.walk === 'normal' ? flowAnswers.walk : null
  const walk: Walk = flowWalk ?? 'normal'

  // the stop nearest the rider, from one fix through places/nearby (the only place a coordinate goes): memory only.
  // The plan's user block then carries that place id, never the fix (CHANGES 2). Bus mode on purpose, for trains
  // too: a stop cluster is a point the meet walks from to every station ahead (meet.station_verdict), whereas a
  // station would read as "the rider chose S" and, with no fix sent, as too far from it.
  const [near, setNear] = useState<{ place_id: string } | null>(null)
  const [nearState, setNearState] = useState<'idle' | 'looking' | 'none'>('idle')
  const looking = useRef(false) // one lookup at a time (a tap and the reload path can both ask)
  const findNear = useCallback(async () => {
    if (looking.current) return
    looking.current = true
    setNearState('looking')
    try {
      // ?near (stage) and a recorded set answer the same wherever the phone is: no location is asked for
      const noFix = Boolean(runtime().near) || fixtureData()
      const fix = noFix ? null : await getFix(6000)
      if (!fix && !noFix) {
        setNear(null)
        setNearState('none')
        return
      }
      const { data } = await placesNearby({ lat: fix?.lat, lon: fix?.lon, mode: 'bus' })
      const p = data.places[0]
      // further than this, the API's walk from that stop would no longer be the rider's walk
      if (p && (p.distance_m ?? 0) <= 400) {
        setNear({ place_id: p.place_id })
        setNearState('idle')
      } else {
        setNear(null)
        setNearState('none')
      }
    } catch {
      setNearState('none')
    } finally {
      looking.current = false
    }
  }, [])

  // ------------------------------------------------------------------------------------------- the request
  const user = useMemo<PlanUser>(() => {
    const u: PlanUser = flowWalk ? { walk: flowWalk } : {}
    if (mode === 'rail') {
      if (answers.where === 'station' && answers.station) Object.assign(u, { place_id: answers.station.place_id, at_station: true })
      else if (answers.where === 'train' && answers.train) u.on_vehicle = { mode: 'rail', vehicle_key: answers.train.vehicle_key }
      else if (answers.where === 'elsewhere' && near) u.place_id = near.place_id
    } else if (mode === 'bus' && near) u.place_id = near.place_id
    return u
  }, [mode, answers.where, answers.station, answers.train, near, flowWalk])

  const car: Car | null = answers.car && answers.car !== 'unsure' ? answers.car : null
  // only the end link's two taps are sent ("yes", and "no" after its Undo); an older visit's stored "maybe" or
  // "Not sure" from the question this link replaced counts as never tapped
  const taken: 'yes' | 'no' | null = answers.taken === 'yes' || answers.taken === 'no' ? answers.taken : null
  const body = useMemo<PlanBody | null>(
    () =>
      loaded
        ? { vehicle_keys: keys, user, ...(taken ? { taken } : {}), ...(car ? { car } : {}), ...(tappedItem ? { item_category: tappedItem } : {}) }
        : null,
    [loaded, keys, user, taken, car, tappedItem],
  )
  const plan = usePlanRequest(sessionId, body)
  const result = plan.result
  const replay = result ? result.source !== 'live' : false

  // after a reload on "Somewhere else", or a live bus match: look up the nearest stop again, but only when the
  // browser already allows location (never a prompt the rider didn't ask for)
  const wantsNear = (mode === 'rail' && answers.where === 'elsewhere') || (mode === 'bus' && keys.length > 0 && result?.source === 'live')
  const autoNear = useRef(false)
  useEffect(() => {
    if (!wantsNear || near || autoNear.current) return
    autoNear.current = true
    locationPermission().then((s) => {
      if (s === 'granted' || runtime().near || fixtureData()) void findNear()
    })
  }, [wantsNear, near, findNear])

  // ----------------------------------------------------------------------------------------------- the meet
  const meet = result?.data.meet?.possible ? result.data.meet : null
  const meetCard = result?.data.cards.find((c) => c.kind === 'meet') ?? null
  const meetMode = keyMode(meet?.vehicle_keys[0]) ?? 'bus'
  const spot: RiderSpot = user.on_vehicle ? 'on_train' : user.at_station ? 'at_station' : 'walking'
  const showingMeet = Boolean(meet && meetCard) && view === 'plan'
  // one watch for the screen (the live blue dot's hook): the meet's walk time, the map's dot and the reunion poll
  const rider = useRiderLocation(view === 'plan')
  const position = meetPosition(rider)
  const walkingFix = showingMeet && spot === 'walking' ? rider.fix : null
  const placeAt = usePlaceCoords(meet?.place_id, meet?.place_label)
  const distance = walkingFix && placeAt ? haversineM(walkingFix, placeAt) : null
  const live = distance !== null && walkingFix ? { distanceM: distance, accuracyM: walkingFix.accuracy_m } : null

  const remaining = useRef<() => number>(() => 0)
  remaining.current = () =>
    remainingWalkS({
      planWalkS: meet?.walk_s ?? 0,
      elapsedS: plan.receivedAt === null ? 0 : (performance.now() - plan.receivedAt) / 1000,
      spot,
      mode: meetMode,
      walk,
      live,
    })
  const watch = useMeetWatch({
    meet,
    enabled: showingMeet,
    remaining: () => remaining.current(),
    riderVehicleKey: user.on_vehicle?.vehicle_key ?? null,
    resetKey: plan.receivedAt,
  })

  // ------------------------------------------------------------------------------------ ways to get it back
  // the plan's reunion, then POST /reunion every 20 s while this screen is open and visible, with the rider's taps
  // and, while they walk, the live fix (reunionModel.reunionBody: in that one request's body, nowhere else)
  const named = keys.length > 0 && (match.status === 'matched' || match.status === 'ambiguous')
  const replaying = result ? result.source !== 'live' : true
  const fixRef = useRef(rider.fix)
  fixRef.current = rider.fix
  const reunionLive = useReunion({
    sessionId,
    initial: result?.data.reunion ?? null,
    initialAt: result ? plan.receivedAt : null,
    enabled: view === 'plan' && named && result !== null,
    body: async () => {
      if (!body) return null
      const f = fixRef.current
      let fix: PollFix | null = f ? { lat: f.lat, lon: f.lon, accuracy_m: f.accuracy_m, age_s: (performance.now() - f.at) / 1000 } : null
      // a rider standing still gets few watch updates: ask once for a fresh fix rather than send a stale one
      if (fix && fix.age_s > 20 && !replaying) {
        const fresh = await getFix(4000)
        if (fresh) fix = { lat: fresh.lat, lon: fresh.lon, accuracy_m: fresh.accuracy_m, age_s: fresh.fix_age_s }
      }
      return reunionBody(body, fix, { replaying })
    },
  })
  const reunion = reunionLive.reunion
  // a meet /now withdrew is not offered again from an older answer
  const skipKinds = useCallback((k: ReunionKind) => watch.withdrawn && (k === 'walk_ahead' || k === 'shared_track'), [watch.withdrawn])

  // a clock for the minutes: the server's (or the replayed) "now" from the latest /now, moved on by wall time
  const [tick, setTick] = useState(0)
  useVisibleInterval(() => setTick((n) => n + 1), 5000, showingMeet)
  const serverNow = useMemo(() => {
    void tick
    const v = watch.latest[0]
    if (v && watch.at !== null) return nowOf(v) + (performance.now() - watch.at)
    if (result && plan.receivedAt !== null) return Date.parse(result.data.generated_at) + (performance.now() - plan.receivedAt)
    return Date.now()
  }, [tick, watch.latest, watch.at, result, plan.receivedAt])

  const liveEta = meet && !tie ? ((watch.latest[0] && meet.place_id ? etaAt(watch.latest[0], meet.place_id) : null) ?? meet.eta?.[0] ?? null) : null
  const meetMinutes = liveEta ? minutesUntil(liveEta, serverNow) : null
  const meetTime = liveEta ? clock(liveEta) : meetCard ? fact(meetCard.facts, 'time') : null

  // -------------------------------------------------------------------------------------------- the cards
  const contacts = useMemo(cardContacts, [])
  const labels = useMemo(reportLabels, [])
  const askWhere = mode === 'rail' && keys.length > 0
  const cards = useMemo(
    () => (result ? arrangeCards(result.data, { taken, withdrawn: watch.withdrawn, contacts, meetSlot: keys.length > 0 }) : []),
    [result, taken, watch.withdrawn, contacts, keys.length],
  )

  // the email and call script: [[ITEM]] from the description typed here (memory only), else the item's own word.
  // The Rail Car Number the rider typed is memory only too: typed text never goes to storage or the network.
  const [item, setItem] = useState('')
  const [carNumber, setCarNumber] = useState<string | null>(null)
  const itemWords = item.trim() || tLoose(`item.${category}` as CopyId)
  const email = result?.data.report.email
  const address = contact('lost_found_email')?.value ?? null
  const emailSubject = email ? fillItem(email.subject, itemWords) : ''
  const emailBody = email ? fillItem(email.body, itemWords) : ''
  const emailHref = email && address ? mailtoHref(address, emailSubject, emailBody) : null

  const whereProps = {
    answers,
    update,
    line,
    bagKeys: keys,
    onElsewhere: () => void findNear(),
    noLocation: nearState === 'none',
  }

  // the map: the best way back pinned beside the vehicle and the rider, with the walking line when they walk there
  const best = mainOption(reunion)
  const target: TargetPoint | null =
    showingMeet && meet?.place_label && placeAt
      ? { lat: placeAt.lat, lon: placeAt.lon, label: t('ways.map_target'), place: meet.place_label, walk: spot === 'walking' }
      : best && !skipKinds(best.kind)
        ? { lat: best.stop_lat, lon: best.stop_lon, label: t('ways.map_target'), place: best.place_label, walk: reunion ? walksThere(best, reunion.rider_location) : false }
        : null
  const wide = useWide()

  // a live bus match: the meet and the waits need to know where the rider is
  const nearOffer =
    mode === 'bus' && keys.length > 0 && result?.source === 'live' && !near && !watch.withdrawn ? (
      <div className={styles.offer}>
        <p>{t('near.offer')}</p>
        {nearState === 'none' ? (
          <p className={styles.note}>{t('where.no_location')}</p>
        ) : (
          <>
            <BigButton
              variant="secondary"
              size="md"
              icon="locate"
              loading={nearState === 'looking'}
              onClick={() => {
                rider.start()
                void findNear()
              }}
            >
              {t('near.use_location')}
            </BigButton>
            <p className={styles.note}>{t('where.near_line')}</p>
          </>
        )}
      </div>
    ) : null

  const carAsk = mode === 'rail' && !tie && best && !skipKinds(best.kind) ? <CarAsk car={answers.car} onCar={(v) => update({ car: v })} /> : null
  const ways = (meetShown: boolean) => (
    <div ref={waysRef}>
      <Ways
        reunion={reunion}
        receivedAt={reunionLive.receivedAt}
        failing={reunionLive.failing}
        live={view === 'plan' && named}
        car={mode === 'rail' && answers.car && answers.car !== 'unsure' ? answers.car : null}
        item={tappedItem}
        meetShown={meetShown}
        skip={skipKinds}
        whereAsked={askWhere || nearOffer !== null}
        extra={meetShown ? null : carAsk}
      />
    </div>
  )
  // "Your best plan": the one way-back card, whichever the API sent (a reunion card or too_far)
  const waysCard = (): CardOverride | null =>
    reunion
      ? {
          title: t('plan.card.best'),
          tone: 'accent',
          // the steps first; "Where are you now?" under them (the plan assumed a place, and says so in its notes)
          body: (
            <>
              {ways(false)}
              {askWhere ? <WhereNow {...whereProps} /> : null}
              {nearOffer}
            </>
          ),
        }
      : null

  const slots: CardSlots = {
    wait_return: waysCard,
    meet_end: waysCard,
    switch_platform: waysCard,
    meet: (card) => ({
      body: (
        <>
          {askWhere ? <WhereNow {...whereProps} /> : null}
          <MeetCard
            card={card}
            mode={meetMode}
            spot={spot}
            time={tie ? fact(card.facts, 'time') : meetTime}
            minutes={tie ? null : meetMinutes}
            walk={spot === 'walking' && live && meet?.place_id ? { minutes: walkMinutes(live.distanceM, walk), there: isThere(live.distanceM, live.accuracyM) } : null}
            position={position}
            car={meetMode === 'rail' && !tie ? answers.car : null}
            onCar={(v) => update({ car: v })}
            slow={walk === 'slow'}
            onSlow={(s) => flow.setExtra({ walk: s ? 'slow' : 'normal' })}
            checking={showingMeet && !watch.withdrawn}
          />
          {reunion ? ways(true) : null}
        </>
      ),
    }),
    too_far: (card) => {
      const w = waysCard()
      if (w) return w
      // no reunion in this answer (an API that predates it): the meet question alone, as before
      if (askWhere && !answers.where) return { title: t('plan.card.meet_check'), tone: 'accent', body: <WhereNow {...whereProps} /> }
      if (nearOffer) return { title: t('plan.card.meet_check'), tone: 'accent', body: nearOffer }
      if (askWhere)
        return {
          body: (
            <>
              <WhereNow {...whereProps} />
              <p>{t('res_04_far')}</p>
            </>
          ),
        }
      return null
    },
    report: (card) =>
      result
        ? {
            body: (
              <ReportCard
                card={card}
                report={result.data.report}
                rail={Boolean(result.data.report.form_fields.rail_line || result.data.report.form_fields.rail_station || mode === 'rail')}
                labels={labels}
                carNumber={carNumber}
                carNumberNo={answers.carNumberNo ?? false}
                onCarNumber={(n, no) => {
                  setCarNumber(n ?? null)
                  update({ carNumberNo: no || undefined })
                }}
                item={item}
                onItem={setItem}
                emailHref={emailHref}
                emailSubject={emailSubject}
                emailBody={emailBody}
              />
            ),
          }
        : null,
    call: (card) =>
      result ? { body: <CallCard card={card} script={result.data.report.call_script} item={itemWords} emailHref={emailHref} nowMs={Date.parse(result.data.generated_at)} /> } : null,
  }

  // ------------------------------------------------------------------------------------- what is announced
  // The meet in the announcement is the card's own lines (meetLines), with the time and minutes the card shows, so
  // a rider on a train hears "Get off at East Point", never the bus card's "Walk there".
  const meetSaid = useRef<() => string | null>(() => null)
  meetSaid.current = () => {
    const m = cards.find((c) => c.kind === 'meet')
    if (!m) return null
    return meetLines({
      card: m,
      mode: meetMode,
      time: tie ? fact(m.facts, 'time') : meetTime,
      minutes: tie ? null : meetMinutes,
      car: meetMode === 'rail' && !tie ? answers.car : null,
    }).join(' ')
  }
  const [said, setSaid] = useState<string | null>(null)
  const firstPlan = useRef(true)
  // "Think someone took it?" puts the safety card at the top, far above the link the rider tapped: it is said
  // straight away (the card shows before the plan comes back), and again with the plan that answered the change
  const safetyNew = useRef(false)
  const wantsSafety = taken === 'yes'
  const lastWantsSafety = useRef<boolean | null>(null)
  useEffect(() => {
    if (!loaded) return
    const before = lastWantsSafety.current
    lastWantsSafety.current = wantsSafety
    if (before === null || before === wantsSafety || !wantsSafety) return
    safetyNew.current = true
    setSaid(`${t('plan.card.safety')}. ${t('wb_12_safe')}`)
  }, [loaded, wantsSafety])
  useEffect(() => {
    if (!result) return
    if (firstPlan.current) {
      firstPlan.current = false
      return
    }
    const safety = safetyNew.current && cards[0]?.kind === 'safety' ? t('wb_12_safe') : null
    safetyNew.current = false
    setSaid([t('plan.updated'), safety, meetSaid.current()].filter(Boolean).join(' '))
  }, [result])
  useEffect(() => {
    if (watch.withdrawn) setSaid(t('meet.withdrawn'))
  }, [watch.withdrawn])
  // a poll that changes the best way says so, politely; times moving inside the same advice are not announced.
  // While the meet card is the main card the best way is not on screen (the card, checked every 10 s, is the
  // advice), so its changes are not announced either: the ear never hears a place the eye can't find.
  const meetIsMain = useRef(false)
  meetIsMain.current = showingMeet && !watch.withdrawn
  useEffect(() => {
    if (meetIsMain.current) return
    const s = changeSaid(reunionLive.previous, reunionLive.reunion)
    if (s) setSaid(s)
  }, [reunionLive.previous, reunionLive.reunion])

  // never jump the layout while the rider reads: when the section changes height above where they are reading
  // (they scrolled on to the report), keep their place. Browsers with scroll anchoring do this themselves.
  const waysRef = useRef<HTMLDivElement>(null)
  const waysHeight = useRef<number | null>(null)
  useLayoutEffect(() => {
    const el = waysRef.current
    if (!el) {
      waysHeight.current = null
      return
    }
    const box = el.getBoundingClientRect()
    const before = waysHeight.current
    waysHeight.current = box.height
    if (before === null || before === box.height) return
    const anchored = typeof CSS !== 'undefined' && CSS.supports?.('overflow-anchor', 'auto')
    if (!anchored && box.bottom - (box.height - before) <= 0) window.scrollBy(0, box.height - before)
  }, [reunionLive.reunion, reunionLive.receivedAt])

  const onClear = async () => {
    await clearSession(sessionId)
    clearPlanAnswers()
    setItem('')
    setCarNumber(null)
    setView('cleared')
  }

  const lead =
    match.status === 'not_reporting'
      ? t('res_notreporting')
      : match.status === 'uncertain' || match.status === 'no_match'
        ? t('res_05_nomatch')
        : topCanceled(match)
          ? t('res_canceled')
          : t('plan.lead')
  // the live map: the right pane on a laptop, the top of the plan on a phone (a match with no named vehicle keeps
  // the clue panel on a laptop and has no map on a phone)
  const planMap = named ? (
    <PlanAside sessionId={sessionId} match={match} keys={keys} source={flow.session?.source ?? 'live'} rider={rider} target={target} item={tappedItem} wide={wide} />
  ) : null
  const aside = wide && planMap ? planMap : undefined

  // the three parts: the theft safety card (above the map), "Your best plan", and "Didn't find it?" for the rest
  const sections = planSections(cards)
  const hasWay = (showingMeet && !watch.withdrawn) || Boolean(best && !skipKinds(best.kind))
  // collapsed while there is a way back to try, open when there isn't; the rider's own tap wins, per session
  const backupOpen = answers.backup ?? !hasWay
  const wayOverride = sections.way ? (slots[sections.way.kind]?.(sections.way) ?? null) : null
  const banner = result && replay ? <ReplayBanner asOf={result.data.generated_at} withHint={false} /> : null
  const footer = (
    <>
      <SafetyFooter />
      <ProofFooter />
    </>
  )

  if (view === 'done')
    return (
      <Screen
        stepKey="plan_done"
        title={t('plan_05_done')}
        aside={aside}
        chip={<SourceChip />}
        banner={banner}
        footer={footer}
        actions={
          // no icon: with one, the label wrapped to two lines at 320 px and the taller thumb zone covered the footer
          <BigButton variant="secondary" onClick={() => setView('plan')}>
            {t('plan.see_again')}
          </BigButton>
        }
      >
        <Mark size={88} className={styles.endMark} />
        <ClearNow deletesBy={session.deletes_by} onClear={onClear} />
      </Screen>
    )

  return (
    <Screen
      stepKey="plan"
      title={t('plan.title')}
      lead={lead}
      aside={aside}
      onBack={flow.state.history.length ? flow.back : null}
      chip={<SourceChip />}
      banner={banner}
      footer={footer}
      actions={
        plan.loading && result ? (
          <p className={styles.updating} role="status">
            <Spinner size="sm" />
            {t('plan.updating')}
          </p>
        ) : undefined
      }
    >
      {result
        ? sections.safety.map((c) => <OneCard key={`safety-${c.step}`} card={c} replay={replay} />)
        : null}
      {wide ? null : planMap}

      {plan.error && !plan.loading && plan.error.error === 'not_found' ? (
        // the session is gone (cleared in another tab, or past retention): the same request can only 404 again
        <>
          <ErrorState error={plan.error} title={t('plan.error_title')} />
          <BigButton variant="primary" onClick={flow.restart}>
            {t('plan.start_over')}
          </BigButton>
        </>
      ) : plan.error && !plan.loading ? (
        <ErrorState
          error={plan.error}
          title={t('plan.error_title')}
          onRetry={plan.retry}
          onSeeRecorded={() => {
            // the whole recorded ride, from its match: a recorded plan for this live session would mix two rides
            openRecordedExample()
            flow.goTo('done')
          }}
        />
      ) : null}

      {result ? (
        <>
          {sections.way ? <OneCard card={sections.way} replay={replay} override={wayOverride} title={t('plan.card.best')} tone="accent" /> : null}
          <ItemAsk item={itemAnswer} onItem={(v) => flow.setExtra({ what: v ?? null })} />
          {sections.backup.length ? (
            <Backup open={backupOpen} onToggle={() => update({ backup: !backupOpen })}>
              <PlanCards cards={sections.backup} replay={replay} slots={slots} />
            </Backup>
          ) : null}
        </>
      ) : plan.loading || !loaded ? (
        <div className={styles.loading}>
          <Spinner showLabel label={t('plan.loading')} />
          <Skeleton block height={40} />
        </div>
      ) : null}

      {result ? (
        <section className={styles.end} aria-label={t('done')}>
          <Mark size={56} className={styles.endMark} />
          <p className={styles.endText}>{t('plan_05_done')}</p>
          <BigButton variant="primary" icon="check" onClick={() => setView('done')}>
            {t('done')}
          </BigButton>
          <ClearNow deletesBy={session.deletes_by} onClear={onClear} />
        </section>
      ) : null}

      {result ? <TakenLink taken={taken} onTaken={(v) => update({ taken: v })} /> : null}

      <LiveRegion text={said} />
    </Screen>
  )
}

// ------------------------------------------------------------------------------------- taken, quiet at the end

/**
 * "Think someone took it?": riders found it unnecessary as a question up top, so it is one small link at the end of
 * the plan, just above the safety footer. A tap sends taken "yes" (the API puts MARTA Police and 911 first and adds
 * freeze cards) and says what changed, with an Undo that sends "no". The swap keeps focus on the new control.
 */
function TakenLink({ taken, onTaken }: { taken: 'yes' | 'no' | null; onTaken: (v: Taken) => void }) {
  const tapped = useRef(false)
  const ref = useRef<HTMLButtonElement>(null)
  const yes = taken === 'yes'
  useEffect(() => {
    // only after a tap here: a stored answer on load never pulls focus to the bottom of the page
    if (tapped.current) ref.current?.focus({ preventScroll: true })
  }, [yes])
  const tap = (v: Taken) => {
    tapped.current = true
    onTaken(v)
  }
  return (
    <div className={styles.taken}>
      {yes ? (
        <p className={styles.takenAdded}>
          <span>{t('taken.added')}</span>
          <button ref={ref} type="button" className={styles.takenLink} onClick={() => tap('no')}>
            {t('taken.undo')}
          </button>
        </p>
      ) : (
        <button ref={ref} type="button" className={styles.takenLink} onClick={() => tap('yes')}>
          {t('taken.ask')}
        </button>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------------------- "Didn't find it?"

/** Everything for when the way back didn't work (tell staff, keep the details, the report, call, the item's own
 *  steps), behind one full-width button; the steps follow the button when open. */
function Backup({ open, onToggle, children }: { open: boolean; onToggle: () => void; children: ReactNode }) {
  const id = useId()
  return (
    <section className={`${styles.backup} ${open ? styles.backupOpen : ''}`} aria-label={t('backup.label')}>
      <BigButton variant="secondary" iconEnd="next" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        {t('backup.toggle')}
      </BigButton>
      <div id={id} hidden={!open}>
        {children}
      </div>
    </section>
  )
}

// ------------------------------------------------------------------------------------- the item, small, after the plan

const ITEM_ICON: Record<ItemCategory, IconName> = { phone: 'phone', wallet: 'wallet', bag: 'bag', keys: 'key', laptop: 'laptop', other: 'question' }

/**
 * "What did you lose?", optional. It is not asked before the match because it never changes the match; here it adds
 * the item's own steps (a wallet: freeze cards first; a phone: lock or erase it), sent as item_category for this plan.
 * Unanswered, the plan is worded for any item (the unattended bag line is always in it).
 */
function ItemAsk({ item, onItem }: { item: ItemCategory | 'not_sure' | undefined; onItem: (v: ItemCategory | 'not_sure' | undefined) => void }) {
  if (item)
    return (
      <Answered onChange={() => onItem(undefined)} changeLabel={t('answer.change')}>
        {t('wb_01_what')} <strong>{item === 'not_sure' ? t('not_sure') : t(`wb_01_what.${item}`)}</strong>
      </Answered>
    )
  return (
    <section className={styles.itemSmall}>
      <QuickChoice<ItemCategory | 'not_sure'>
        headingLevel={2}
        question={t('wb_01_what')}
        hint={t('plan.item_hint')}
        layout="grid"
        // optional, so no "Not sure": leaving it is the same answer, and the plan is already worded for any item
        options={ITEMS.map((v) => ({ value: v, label: t(`plan.item.${v}`), icon: ITEM_ICON[v] }))}
        onChoose={onItem}
      />
    </section>
  )
}

// ----------------------------------------------------------------------------- the car, for a train's platform

/** "Which part of the train?" beside a train's way back: the platform hint (sent as `car`, for this plan only). */
function CarAsk({ car, onCar }: { car: Car | 'unsure' | undefined; onCar: (v: Car | 'unsure' | undefined) => void }) {
  if (car)
    return (
      <Answered onChange={() => onCar(undefined)} changeLabel={t('answer.change')}>
        {t('wb_10_car')} <strong>{car === 'unsure' ? t('not_sure') : t(`wb_10_car.${car}`)}</strong>
      </Answered>
    )
  return (
    <QuickChoice<Car | 'unsure'>
      question={t('wb_10_car')}
      options={[
        { value: 'front', label: t('wb_10_car.front') },
        { value: 'middle', label: t('wb_10_car.middle') },
        { value: 'back', label: t('wb_10_car.back') },
        { value: 'unsure', label: t('not_sure'), quiet: true },
      ]}
      onChoose={onCar}
    />
  )
}
