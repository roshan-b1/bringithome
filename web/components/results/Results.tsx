'use client'

import { useCallback, useMemo, useState, type ReactNode } from 'react'
import type { Intake, Match, MatchCandidate, SessionResponse, VehicleKey, VehicleNow } from '@/contracts/api'
import { Banner, ReplayBanner } from '@/components/ui/Banner'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { ChoiceList, type Choice } from '@/components/ui/ChoiceList'
import { lineColor } from '@/components/ui/LineBadge'
import { Screen } from '@/components/ui/Screen'
import { createSession, directions, type ApiResult } from '@/lib/api'
import { t } from '@/lib/copy'
import { useWide } from '@/lib/hooks'
import { clock } from '@/lib/time'
import { CandidateCard } from './CandidateCard'
import { ContextLine } from './ContextLine'
import {
  evidenceLine,
  headline,
  headlineParts,
  isLine,
  levelWord,
  lineWords,
  namedVehicleKeys,
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

// The match result, one view per status (decision 7's table, CHANGES 1):
//   matched        the vehicle, the replay map, its evidence and where it is now
//   ambiguous      the tie_break as ONE question ("Which one were you on?"); the answer re-runs the match
//   uncertain      separate_by as ONE question (route, direction or where they got on); the answer re-runs it
//   not_reporting  the timetable's buses, labeled schedule only, with "probably the same bus"
//   no_match       the plan anyway
// The question is asked once. Whatever the re-run says is what shows: a tie it couldn't split shows both (the plan
// covers both), an uncertain one shows its closest fits and names none. So the plan, which reads the session, always
// agrees with this screen. Nothing here is invented: names, times, distances and states come from the session, the
// replay and /now, and the rider's distance from their own fix, which never leaves the phone.

export interface ResultsProps {
  session: ApiResult<SessionResponse>
  /** the intake that produced the session (or one rebuilt from the tap answers); null turns re-running off */
  intake: Intake | null
  /** a re-run made a new session: store it (flow.setSession) */
  onSession: (result: ApiResult<SessionResponse>) => void
  /** on to the plan, with the vehicles this session names (0 to 2; the plan's vehicle_keys) */
  onContinue: (vehicleKeys: VehicleKey[]) => void
  /** no_match: back to the questions to change an answer */
  onEdit?: (() => void) | null
  onBack?: (() => void) | null
}

type View = 'matched' | 'tie' | 'tie_open' | 'separate' | 'uncertain' | 'not_reporting' | 'no_match'

const lowerFirst = (s: string) => (s ? s.charAt(0).toLowerCase() + s.slice(1) : s)
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

export function Results({ session, intake, onSession, onContinue, onEdit, onBack }: ResultsProps) {
  const s = session.data
  const match = s.match
  const [answeredHere, setAnsweredHere] = useState<string | null>(null) // the session id the answer produced
  const [busy, setBusy] = useState<string | null>(null)
  const [refineFailed, setRefineFailed] = useState(false)
  const [nows, setNows] = useState<Record<VehicleKey, VehicleNow | null>>({})

  // read per session: a reload of the re-run session must not ask again (resultsStore)
  const asked = useMemo(() => answeredHere === s.session_id || wasAsked(s.session_id), [answeredHere, s.session_id])
  const view = viewOf(match, asked)
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
    [intake, s, onSession],
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
  const main = view === 'matched' ? (match.candidates[0] ?? null) : null

  if (main) {
    const parts = headlineParts(main)
    titleNode = (
      <>
        <span className={styles.titleLead}>{parts.lead}</span> {parts.name}
      </>
    )
    announce = `${headline(main)}. ${t('results.how_sure', { level: levelWord(main.level) })}`
    body = (
      <>
        <ContextLine match={match} />
        {place(map([main.vehicle_key], 'match'))}
        <CandidateCard c={main} tone="accent" />
        {place(live(main, false))}
      </>
    )
    actions = seePlan
  } else if (view === 'tie') {
    const both = candidatesFor(match, tieKeys(match))
    titleNode = t('tie.title')
    lead = t('tie.lead')
    announce = `${t('tie.title')} ${lead}`
    const choices: Choice<VehicleKey>[] = both.map((c) => {
      const line = lineOf(c)
      return {
        value: c.vehicle_key,
        label: t('tie.option', { vehicle: vehicleName(c), direction: lowerFirst(c.direction_label) }),
        sublabel: c.evidence[0] ? evidenceLine(c.evidence[0]) : undefined,
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
    titleNode = t('results.nomatch_title')
    lead = t('separate.lead')
    const question = sep.question === 'route' ? t('wb_08_bus') : sep.question === 'direction' ? t('wb_09_way') : t('separate.board_place')
    announce = `${t('results.nomatch_title')} ${lead} ${question}`
    const choices: Choice<string>[] = sep.options.map((o) => {
      const cands = match.candidates.filter((c) => o.vehicle_keys.includes(c.vehicle_key))
      const lines = [...new Set(cands.map(lineOf))]
      const railLine = cands.length && cands.every((c) => c.mode === 'rail') && lines.length === 1 ? lines[0] : null
      const times = cands.map((c) => c.evidence[0]?.at).filter((x): x is string => !!x).sort().map((x) => clock(x))
      return {
        value: o.label,
        label: sep.question === 'route' && railLine ? lineWords(railLine) : o.label,
        sublabel: times.length ? t('separate.times', { times: [...new Set(times)].join(t('separate.or')) }) : undefined,
        swatch: sep.question === 'route' && railLine ? lineColor(railLine) : undefined,
        icon: sep.question === 'route' && !railLine ? 'bus' : undefined,
      }
    })
    body = (
      <>
        <section className={styles.question} aria-labelledby="separate-q">
          <h2 id="separate-q" className={styles.questionTitle}>
            {question}
          </h2>
          {sep.question === 'direction' ? <p className={styles.muted}>{t('wb_09_way.hint')}</p> : null}
          <ChoiceList
            labelledBy="separate-q"
            choices={choices}
            busy={busy}
            notSure={t('separate.not_sure')}
            onChoose={(v) => {
              const option = sep.options.find((o) => o.label === v)
              if (v === 'not_sure' || !option) onContinue([])
              else void refine({ kind: 'separate', question: sep.question, option }, v)
            }}
          />
          {busy ? <p className={styles.checking}>{t('refine.checking')}</p> : null}
        </section>
        <Fits match={match} />
        {place(map(replayVehicleKeys(match), 'candidate'))}
      </>
    )
  } else if (view === 'uncertain' || view === 'separate') {
    titleNode = t('results.nomatch_title')
    lead = t('results.nomatch_lead')
    announce = `${t('results.nomatch_title')} ${lead}`
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

/** Decision 7's uncertain view: the top 3 with their times, none of them named as the match. */
function Fits({ match }: { match: Match }) {
  const top = match.candidates.slice(0, 3)
  if (top.length === 0) return null
  return (
    <section className={styles.section} aria-labelledby="fits-heading">
      <h2 id="fits-heading" className={styles.h2}>
        {t('separate.fits_heading')}
      </h2>
      <ul role="list" className={styles.fits}>
        {top.map((c) => (
          <CandidateCard key={c.vehicle_key} c={c} compact as="li" />
        ))}
      </ul>
    </section>
  )
}
