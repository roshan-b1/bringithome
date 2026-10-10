'use client'

import { useCallback, useMemo, useState, type ReactNode } from 'react'
import type { Intake, Match, MatchCandidate, SeparateBy, SessionResponse, VehicleKey, VehicleNow } from '@/contracts/api'
import { Banner, ReplayBanner } from '@/components/ui/Banner'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { ChoiceList, type Choice } from '@/components/ui/ChoiceList'
import { lineColor } from '@/components/ui/LineBadge'
import { Screen } from '@/components/ui/Screen'
import { createSession, directions, type ApiResult } from '@/lib/api'
import { t } from '@/lib/copy'
import { useWide } from '@/lib/hooks'
import { CandidateCard, LevelMeter } from './CandidateCard'
import { idLine, PickCard, VehicleHero } from './PickCard'
import { Card } from '@/components/ui/Card'
import { ContextLine } from './ContextLine'
import {
  headline,
  isLine,
  levelWord,
  lineWords,
  namedVehicleKeys,
  passedClock,
  pickRow,
  pickTitle,
  pickVehicle,
  replayVehicleKeys,
  scheduleKind,
  tieKeys,
  vehicleName,
} from './format'
import { LiveVehicle } from './LiveVehicle'
import { MatchMap } from './MatchMap'
import { buildRefinedIntake, type FetchDirections, type RefineAnswer } from './refine'
import { markAsked, wasAsked } from './resultsStore'
import { ScheduleList } from './ScheduleList'
import { useRiderLocation } from './useRiderLocation'
import styles from './Results.module.css'

// The match result, one view per status (decision 7's table, CHANGES 1). Riders want ONE answer, not a list:
//   matched        "Your train" (or bus): that vehicle alone, big, with its line or route, which way it went and
//                  where and when it passed, the replay map and where it is now. No list
//   ambiguous and  "Which train were you on?" (bus, or one, for a mix), leading with the single most likely one as
//   uncertain      "Most likely yours": "Yes, that's it" picks it, "No, show the others" shows every choice as a big
//                  tap target (line badge, which way, when it passed), plus the matcher's one question (separate_by:
//                  route, direction or where they got on; its answer re-runs the match) or planning without a pick
//   not_reporting  the timetable's buses, labeled schedule only, with "probably the same bus"
//   no_match       the plan anyway
// Several trains pass a busy stop in any 10 minutes, so those headings are a calm question, never a failure. A tap on
// a vehicle (or a "which way" answer that covers exactly one) is the rider's own pick: onPick goes straight to the
// plan for it, which the API then plans as the match (picked, lib/riderPick.ts), so a train the rider didn't choose
// is never shown after they chose one. The question is asked once; a re-run shows its own most likely one.
// Without onPick (the results harness) the older views stand: the tie as one question, the closest fits as cards.
// Nothing here is invented: names, times, distances and states come from the session, the replay and /now, and the
// rider's distance from their own fix, which never leaves the phone.

export interface ResultsProps {
  session: ApiResult<SessionResponse>
  /** the intake that produced the session (or one rebuilt from the tap answers); null turns re-running off */
  intake: Intake | null
  /** a re-run made a new session: store it (flow.setSession) */
  onSession: (result: ApiResult<SessionResponse>) => void
  /** on to the plan, with the vehicles this session names (0 to 2; the plan's vehicle_keys) */
  onContinue: (vehicleKeys: VehicleKey[]) => void
  /** the rider tapped one vehicle themselves (a closest fit, or a tie option the re-run couldn't name): on to the
   *  plan for it, sent as picked. Without it the closest fits are shown as plain cards */
  onPick?: ((vehicleKey: VehicleKey) => void) | null
  /** no_match: back to the questions to change an answer */
  onEdit?: (() => void) | null
  onBack?: (() => void) | null
}

type View = 'matched' | 'tie' | 'tie_open' | 'separate' | 'uncertain' | 'not_reporting' | 'no_match'

const lineOf = (c: Pick<MatchCandidate, 'line' | 'route'>) => c.line ?? (isLine(c.route) ? c.route : null)

function viewOf(match: Match, asked: boolean): View {
  switch (match.status) {
    case 'matched':
      return 'matched'
    case 'ambiguous':
      return asked ? 'tie_open' : 'tie'
    case 'uncertain':
      return !asked && match.separate_by?.options.length ? 'separate' : 'uncertain'
    case 'not_reporting':
      return 'not_reporting'
    case 'no_match':
      return 'no_match'
  }
}

const candidatesFor = (match: Match, keys: readonly VehicleKey[]): MatchCandidate[] =>
  keys.map((k) => match.candidates.find((c) => c.vehicle_key === k)).filter((c): c is MatchCandidate => !!c)

const fetchDirections: FetchDirections = async (q) => (await directions(q)).data.options

export function Results({ session, intake, onSession, onContinue, onPick = null, onEdit, onBack }: ResultsProps) {
  const s = session.data
  const match = s.match
  const [answeredHere, setAnsweredHere] = useState<string | null>(null) // the session id the answer produced
  const [busy, setBusy] = useState<string | null>(null)
  const [refineFailed, setRefineFailed] = useState(false)
  const [nows, setNows] = useState<Record<VehicleKey, VehicleNow | null>>({})

  // read per session: a reload of the re-run session must not ask again (resultsStore)
  const asked = useMemo(() => answeredHere === s.session_id || wasAsked(s.session_id), [answeredHere, s.session_id])
  const view = viewOf(match, asked)
  const [othersFor, setOthersFor] = useState<string | null>(null) // "No, show the others", per session
  const showOthers = othersFor === s.session_id
  const likelyView = view === 'tie' || view === 'tie_open' || view === 'separate' || view === 'uncertain'
  // the choices, most likely first: a tie's two, then the rest of the candidates (at most 4 in all)
  const pool = useMemo(() => {
    const first = match.status === 'ambiguous' ? candidatesFor(match, tieKeys(match)) : []
    return [...first, ...match.candidates.filter((c) => !first.includes(c))].slice(0, 4)
  }, [match])
  const hasMap = view !== 'not_reporting' && view !== 'no_match' && match.candidates.length > 0
  const rider = useRiderLocation(hasMap)

  const refine = useCallback(
    async (answer: RefineAnswer, value: string) => {
      setBusy(value)
      setRefineFailed(false)
      try {
        const { intake: next } = await buildRefinedIntake(intake, s, answer, fetchDirections)
        if (next) {
          const result = await createSession(next)
          markAsked(result.data.session_id)
          setAnsweredHere(result.data.session_id)
          onSession(result)
          // the rider tapped one of a tie: unless the re-run names exactly that one, their tap stands (never show a
          // train they didn't choose after they chose one)
          const m = result.data.match
          const key = answer.kind === 'tie' ? answer.vehicleKey : null
          const namesIt = m.status === 'matched' && m.candidates[0]?.vehicle_key === key
          if (key && onPick && !namesIt && m.candidates.some((c) => c.vehicle_key === key)) onPick(key)
        } else {
          // nothing the answer can add to the intake: show this session as it is, without the question
          markAsked(s.session_id)
          setAnsweredHere(s.session_id)
        }
      } catch {
        setRefineFailed(true) // the question stays; the rider can tap again or "Not sure"
      } finally {
        setBusy(null)
      }
    },
    [intake, s, onSession, onPick],
  )

  // the laptop: the map and the live view go to the right pane (one map either way: the phone keeps them inline)
  const wide = useWide()
  const side: ReactNode[] = []
  const place = (node: ReactNode): ReactNode => {
    if (!wide || !node) return node
    side.push(node)
    return null
  }

  const onNow = useCallback((n: VehicleNow | null) => {
    if (n) setNows((prev) => (prev[n.vehicle_key] === n ? prev : { ...prev, [n.vehicle_key]: n }))
  }, [])

  // ------------------------------------------------------------------------------------------------ per view
  let titleNode: ReactNode
  let lead: string | null = null
  let announce: string
  let body: ReactNode
  let actions: ReactNode = null
  const seePlan = (
    <BigButton variant="primary" iconEnd="next" onClick={() => onContinue(namedVehicleKeys(match))}>
      {t('results.see_plan')}
    </BigButton>
  )
  const map = (keys: VehicleKey[], tone: 'match' | 'candidate') =>
    hasMap ? (
      <MatchMap
        key={`map:${keys.join(',')}`}
        sessionId={s.session_id}
        match={match}
        vehicleKeys={keys}
        tone={tone}
        tall={wide}
        now={nows}
        nowSource={session.source}
        rider={rider.fix}
        riderStatus={rider.status}
        onShowMe={rider.start}
      />
    ) : null
  const live = (c: MatchCandidate, named: boolean) => (
    <LiveVehicle
      key={`now:${c.vehicle_key}`}
      vehicleKey={c.vehicle_key}
      title={named ? t('now.title_named', { vehicle: [vehicleName(c), c.label && c.mode === 'bus' ? c.label : null].filter(Boolean).join(' ') }) : undefined}
      rider={rider.fix}
      riderStatus={rider.status}
      onNow={onNow}
    />
  )
  // separate_by as ONE question; an answer that covers one train or bus is the rider pointing at it (their pick)
  const separateQuestion = (sep: SeparateBy) => {
    const choices: Choice<string>[] = sep.options.map((o) => {
      const cands = match.candidates.filter((c) => o.vehicle_keys.includes(c.vehicle_key))
      const lines = [...new Set(cands.map(lineOf))]
      const railLine = cands.length && cands.every((c) => c.mode === 'rail') && lines.length === 1 ? lines[0] : null
      return {
        value: o.label,
        label: sep.question === 'route' && railLine ? lineWords(railLine) : o.label,
        sublabel: cands.length ? <Passes cands={cands} /> : undefined,
        swatch: sep.question === 'route' && railLine ? lineColor(railLine) : undefined,
        icon: sep.question === 'route' && !railLine ? 'bus' : undefined,
      }
    })
    return (
      <section key="separate" className={styles.question} aria-labelledby="separate-q">
        <h2 id="separate-q" className={styles.questionTitle}>
          {questionText(sep)}
        </h2>
        {sep.question === 'direction' ? <p className={styles.muted}>{t('wb_09_way.hint')}</p> : null}
        <ChoiceList
          labelledBy="separate-q"
          choices={choices}
          busy={busy}
          notSure={t('separate.not_sure')}
          onChoose={(v) => {
            const option = sep.options.find((o) => o.label === v)
            const only = option?.vehicle_keys.length === 1 ? option.vehicle_keys[0]! : null
            if (v === 'not_sure' || !option) onContinue([])
            else if (only && onPick && match.candidates.some((c) => c.vehicle_key === only)) onPick(only)
            else void refine({ kind: 'separate', question: sep.question, option }, v)
          }}
        />
        {busy ? <p className={styles.checking}>{t('refine.checking')}</p> : null}
      </section>
    )
  }

  const main = view === 'matched' ? (match.candidates[0] ?? null) : null

  if (main) {
    // one fits: that vehicle alone, big ("Your train", its line, which way, where and when it passed), no list
    titleNode = main.mode === 'rail' ? t('pick.your_train') : t('pick.your_bus')
    announce = `${headline(main)}. ${t('results.how_sure', { level: levelWord(main.level) })}`
    body = (
      <>
        <Card tone="accent" className={styles.hero}>
          <VehicleHero c={main} />
          <LevelMeter level={main.level} />
        </Card>
        {place(map([main.vehicle_key], 'match'))}
        {place(live(main, false))}
      </>
    )
    actions = seePlan
  } else if (onPick && likelyView && pool.length > 0) {
    // uncertain or ambiguous: lead with the one most likely, "Yes, that's it" or "No, show the others" (then each of
    // them to tap, the one question, and planning without a pick). A tap is the rider's pick: straight to its plan
    const top = pool[0]!
    const title = pickTitle(pool)
    titleNode = title
    lead = t('pick.lead')
    const sep = view === 'separate' ? (match.separate_by ?? null) : null
    if (!showOthers) {
      announce = `${title} ${lead} ${t('pick.likely')}: ${headline(top)}`
      body = (
        <>
          <section className={styles.section} aria-labelledby="likely-heading">
            <h2 id="likely-heading" className={styles.h2}>
              {t('pick.likely')}
            </h2>
            <Card tone="accent" className={styles.hero}>
              <VehicleHero c={top} />
            </Card>
          </section>
          {place(map([top.vehicle_key], 'candidate'))}
        </>
      )
      actions = (
        <>
          <BigButton variant="primary" iconEnd="next" onClick={() => onPick(top.vehicle_key)}>
            {t('pick.yes')}
          </BigButton>
          <BigButton variant="secondary" onClick={() => setOthersFor(s.session_id)}>
            {t('pick.no')}
          </BigButton>
        </>
      )
    } else {
      announce = `${title} ${t('pick.others')}`
      body = (
        <>
          <section className={styles.section} aria-labelledby="fits-heading">
            <h2 id="fits-heading" className={styles.h2}>
              {t('pick.others')}
            </h2>
            {sep ? <p className={styles.muted}>{t('pick.others_lead')}</p> : null}
            <ul role="list" className={styles.fits}>
              {pool.map((c) => (
                <li key={`pick:${c.vehicle_key}`}>
                  <PickCard c={c} onPick={onPick} disabled={busy !== null} />
                </li>
              ))}
            </ul>
          </section>
          {sep ? separateQuestion(sep) : null}
          <ContextLine match={match} />
          {place(map(view === 'tie' || view === 'tie_open' ? tieKeys(match) : replayVehicleKeys(match), 'candidate'))}
        </>
      )
      // no question left: plan without a pick (both, on a tie)
      actions = sep ? null : (
        <BigButton variant="quiet" size="md" onClick={() => onContinue(namedVehicleKeys(match))}>
          {match.status === 'ambiguous' ? t('pick.plan_both') : t('separate.not_sure')}
        </BigButton>
      )
    }
  } else if (view === 'tie') {
    const both = candidatesFor(match, tieKeys(match))
    const title = pickTitle(both)
    titleNode = title
    lead = t('tie.lead')
    announce = `${title} ${lead}`
    const choices: Choice<VehicleKey>[] = both.map((c) => {
      const line = lineOf(c)
      const id = idLine(c)
      return {
        value: c.vehicle_key,
        label: pickRow(c),
        sublabel: id ?? undefined,
        swatch: c.mode === 'rail' && line ? lineColor(line) : undefined,
        icon: c.mode === 'rail' ? (line ? undefined : 'train') : 'bus',
      }
    })
    body = (
      <>
        <ChoiceList
          labelledBy="results-title"
          choices={choices}
          busy={busy as VehicleKey | null}
          notSure={t('tie.not_sure')}
          onChoose={(v) => {
            if (v === 'not_sure') onContinue(namedVehicleKeys(match))
            else void refine({ kind: 'tie', vehicleKey: v }, v)
          }}
        />
        {busy ? <p className={styles.checking}>{t('refine.checking')}</p> : null}
        <ContextLine match={match} />
        {place(map(tieKeys(match), 'match'))}
      </>
    )
  } else if (view === 'tie_open') {
    // (without onPick only: the results harness) a tie the re-run couldn't split, both shown, the plan covers both
    const both = candidatesFor(match, tieKeys(match))
    titleNode = t('tie.open_title')
    lead = t('tie.open_lead')
    announce = `${t('tie.open_title')} ${lead}`
    body = (
      <>
        <ContextLine match={match} />
        {place(map(tieKeys(match), 'match'))}
        {both.map((c) => (
          <CandidateCard key={`card:${c.vehicle_key}`} c={c} tone="accent" />
        ))}
        {place(<div key="lives" className={styles.lives}>{both.map((c) => live(c, true))}</div>)}
      </>
    )
    actions = seePlan
  } else if (view === 'separate' && match.separate_by) {
    const sep = match.separate_by
    const title = pickTitle(fitsOf(match))
    titleNode = title
    lead = t('separate.lead')
    announce = `${title} ${lead} ${questionText(sep)}`
    body = (
      <>
        {separateQuestion(sep)}
        <Fits match={match} />
        {place(map(replayVehicleKeys(match), 'candidate'))}
      </>
    )
  } else if (view === 'uncertain' || view === 'separate') {
    const fits = fitsOf(match)
    const title = fits.length ? pickTitle(fits) : t('results.nomatch_title')
    titleNode = title
    lead = t('results.nomatch_lead')
    announce = `${title} ${lead}`
    body = (
      <>
        <ContextLine match={match} />
        <Fits match={match} />
        {place(map(replayVehicleKeys(match), 'candidate'))}
      </>
    )
    actions = seePlan
  } else if (view === 'not_reporting') {
    const kind = scheduleKind(match, intake?.mode)
    const title =
      kind === 'streetcar' ? t('results.streetcar_title') : kind === 'train' ? t('results.notreporting_title_train') : t('results.notreporting_title')
    titleNode = title
    lead = kind === 'streetcar' ? t('results.streetcar_lead') : t('results.notreporting_lead')
    announce = `${title} ${lead}`
    body = (
      <>
        {/* the API counts buses here; the streetcar isn't in the live feed at all, so "no buses passed" would mislead */}
        {kind === 'streetcar' ? null : <ContextLine match={match} />}
        <ScheduleList items={match.schedule_candidates} />
      </>
    )
    actions = seePlan
  } else {
    titleNode = t('results.nomatch_title')
    lead = t('results.nomatch_lead')
    announce = `${t('results.nomatch_title')} ${lead}`
    body = (
      <>
        <ContextLine match={match} />
        <ScheduleList items={match.schedule_candidates} />
      </>
    )
    actions = (
      <>
        {seePlan}
        {onEdit ? (
          <BigButton variant="quiet" size="md" onClick={onEdit}>
            {t('results.change_answers')}
          </BigButton>
        ) : null}
      </>
    )
  }

  const asOf = s.as_of ?? session.asOf
  return (
    <Screen
      stepKey={`results:${s.session_id}:${view}`}
      title={<span id="results-title">{titleNode}</span>}
      lead={lead}
      announce={announce}
      onBack={onBack ?? null}
      chip={<SourceChip />}
      banner={
        <>
          {asOf ? <ReplayBanner asOf={asOf} withHint={false} /> : null}
          {refineFailed ? <Banner tone="warning">{t('refine.failed')}</Banner> : null}
        </>
      }
      actions={actions}
      footer={<p className={styles.credit}>{t('data_credit')}</p>}
      aside={side.length ? <div className={styles.side}>{side}</div> : undefined}
    >
      {body}
    </Screen>
  )
}

/** The closest fits decision 7's uncertain view shows: the top 3, none of them named as the match by us. */
const fitsOf = (match: Match): MatchCandidate[] => match.candidates.slice(0, 3)

const questionText = (sep: SeparateBy): string =>
  sep.question === 'route' ? t('wb_08_bus') : sep.question === 'direction' ? t('wb_09_way') : t('separate.board_place')

/**
 * The closest fits. With onPick each is a big tap target (the rider's own pick, on to the plan for it); without it
 * they are plain cards with their times, as decision 7 first had them.
 */
function Fits({ match, onPick, disabled = false }: { match: Match; onPick?: ((key: VehicleKey) => void) | null; disabled?: boolean }) {
  const top = fitsOf(match)
  if (top.length === 0) return null
  return (
    <section className={styles.section} aria-labelledby="fits-heading">
      <h2 id="fits-heading" className={styles.h2}>
        {t('separate.fits_heading')}
      </h2>
      <ul role="list" className={styles.fits}>
        {top.map((c) =>
          onPick ? (
            <li key={c.vehicle_key}>
              <PickCard c={c} onPick={onPick} disabled={disabled} />
            </li>
          ) : (
            <CandidateCard key={c.vehicle_key} c={c} compact as="li" />
          ),
        )}
      </ul>
    </section>
  )
}

/** Under a "which way" (or route, or place) option: each train or bus it covers, by line colour and name, and when it
 *  passed ("Red Line, passed 3:23 PM"), earliest first. */
function Passes({ cands }: { cands: readonly MatchCandidate[] }) {
  const sorted = [...cands].sort((a, b) => (a.evidence[0]?.at ?? '').localeCompare(b.evidence[0]?.at ?? ''))
  return (
    <span className={styles.passes}>
      {sorted.map((c) => {
        const line = lineOf(c)
        const time = passedClock(c)
        return (
          <span key={c.vehicle_key} className={styles.pass}>
            {c.mode === 'rail' && line ? <span className={styles.passSwatch} style={{ background: lineColor(line) }} aria-hidden="true" /> : null}
            {time ? t('pick.passed', { vehicle: pickVehicle(c), time }) : pickVehicle(c)}
          </span>
        )
      })}
    </span>
  )
}
