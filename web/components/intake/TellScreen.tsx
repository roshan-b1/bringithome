'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { BigButton } from '@/components/ui/BigButton'
import { SourceChip } from '@/components/ui/Chip'
import { errorMessage } from '@/components/ui/ErrorState'
import { keepFocusAt, Screen } from '@/components/ui/Screen'
import { ApiError, parse } from '@/lib/api'
import { t } from '@/lib/copy'
import { proposalEmpty, proposeFromParse, type Proposal } from '@/lib/flow'
import { useOnline } from '@/lib/hooks'
import { backOf, IntakeBanner, Note, type IntakeProps } from './parts'
import { useSpeech } from './speech'
import { TellConfirm } from './TellConfirm'
import styles from './intake.module.css'

// TellMe, "Type it instead" (decision 11): where and when in the rider's own words, typed or spoken, read by
// POST /api/parse (Gemini with a rule-based fallback on the server; places and times come only from the API's
// resolver and time parser), then confirmed by tap on the next view. The words live in this component's memory and
// in the one POST body, never in sessionStorage. If /api/parse isn't there (404 on an older server, or a fixture set
// without parse.json) or can't answer, the rider goes on by tapping, losing nothing.

type Phase =
  | { kind: 'input' }
  | { kind: 'reading' }
  | { kind: 'confirm'; proposal: Proposal }
  | { kind: 'failed'; reason: 'unavailable' | 'nothing' | 'error'; error?: unknown }

const MAX_CHARS = 600 // ParseRequest's limit

export function TellScreen({ flow }: IntakeProps) {
  const a = flow.state.answers
  const [text, setText] = useState('')
  const [phase, setPhase] = useState<Phase>({ kind: 'input' })
  const online = useOnline()
  const id = useId()
  const before = useRef('') // what was typed before the mic started, so speech adds to it instead of replacing it
  const speech = useSpeech((heard) => setText(`${before.current}${before.current && heard ? ' ' : ''}${heard}`.slice(0, MAX_CHARS)))

  const submit = async () => {
    const clue = text.trim()
    if (!clue) return
    speech.stop()
    setPhase({ kind: 'reading' })
    try {
      const r = await parse(clue.slice(0, MAX_CHARS))
      const p = proposeFromParse(r.data.fields, flow.state.answers)
      setPhase(proposalEmpty(p) ? { kind: 'failed', reason: 'nothing' } : { kind: 'confirm', proposal: p })
    } catch (e) {
      if (e instanceof ApiError && e.error === 'aborted') return
      const retryable = e instanceof ApiError && (e.error === 'rate_limited' || e.error === 'timeout')
      setPhase({ kind: 'failed', reason: retryable ? 'error' : 'unavailable', error: e })
    }
  }

  // a failure swaps the buttons under the tapped one: focus goes to what it says, not to <body>
  const failedNote = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (phase.kind === 'failed') keepFocusAt(failedNote.current)
  }, [phase.kind])

  if (phase.kind === 'confirm') return <TellConfirm flow={flow} proposal={phase.proposal} onAgain={() => setPhase({ kind: 'input' })} />

  const unavailable = phase.kind === 'failed' && phase.reason === 'unavailable'
  const taps = () => flow.answer('tell', 'taps')

  return (
    <Screen
      stepKey="tell"
      title={t('tell.title')}
      lead={a.what === undefined ? t('tell.lead_item') : t('tell.lead_off')}
      onBack={backOf(flow)}
      chip={<SourceChip />}
      banner={<IntakeBanner />}
      actions={
        unavailable ? (
          <BigButton variant="primary" iconEnd="next" onClick={taps}>
            {t('tell.answer_by_tapping')}
          </BigButton>
        ) : (
          <>
            <BigButton variant="primary" iconEnd="next" disabled={!text.trim()} loading={phase.kind === 'reading'} onClick={submit}>
              {phase.kind === 'reading' ? t('tell.reading') : t('tell.submit')}
            </BigButton>
            <BigButton variant="quiet" size="md" onClick={taps}>
              {t('tell.or_tap')}
            </BigButton>
          </>
        )
      }
    >
      {phase.kind === 'failed' ? (
        <div role="alert" ref={failedNote} tabIndex={-1} className={styles.focusTarget}>
          <Note warm>
            {phase.reason === 'unavailable' ? t('tell.unavailable') : phase.reason === 'nothing' ? t('tell.nothing') : errorMessage(phase.error)}
          </Note>
        </div>
      ) : null}

      <div className={styles.field}>
        <label htmlFor={id} className={styles.label}>
          {t('tell.label')}
        </label>
        <textarea
          id={id}
          className={styles.textarea}
          value={text}
          maxLength={MAX_CHARS}
          rows={4}
          placeholder={t('tell.example')}
          autoComplete="off"
          enterKeyHint="go"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends on a hardware keyboard (the expo table laptop); Shift+Enter is a new line
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              void submit()
            }
          }}
          aria-describedby={speech.supported && online ? `${id}-mic` : undefined}
        />
      </div>

      {speech.supported && online && !unavailable ? (
        <div className={styles.mic}>
          <BigButton
            variant="secondary"
            size="md"
            icon={speech.listening ? 'close' : 'mic'}
            aria-pressed={speech.listening}
            onClick={() => {
              if (speech.listening) return speech.stop()
              before.current = text.trim()
              speech.start()
            }}
          >
            {speech.listening ? t('tell.mic_stop') : t('tell.mic')}
          </BigButton>
          {speech.listening ? (
            <p className={styles.listening} role="status">
              <span className={styles.pulse} aria-hidden="true" />
              {t('tell.listening')}
            </p>
          ) : null}
          <p className={styles.hint} id={`${id}-mic`}>
            {t('tell.mic_disclosure')}
          </p>
        </div>
      ) : null}
    </Screen>
  )
}
