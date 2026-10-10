'use client'

import { useEffect, useId, useRef, useState } from 'react'
import type { Place, QueryMode } from '@/contracts/api'
import { ChoiceList } from '@/components/ui/ChoiceList'
import { errorMessage } from '@/components/ui/ErrorState'
import { Icon } from '@/components/ui/Icon'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { ApiError, placesSearch } from '@/lib/api'
import { t } from '@/lib/copy'
import { Note, PlaceSub, placeIcon } from './parts'
import styles from './intake.module.css'

// "Search a stop or street": debounced GET /api/places/search. The API's resolver is deterministic (aliases, route
// context, fuzzy stop names; never a geocoder or a model), so whatever comes back is a real stop or station. An
// auto-resolved place still needs the rider's tap; needs_confirm shows "Did you mean one of these?".

const DEBOUNCE_MS = 350
const MIN_CHARS = 2

type Result =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; places: Place[]; confirm: boolean }
  | { kind: 'failed'; error: unknown }

export function PlaceSearch({
  mode,
  route,
  onPick,
  selectedId,
  autoFocus = false,
}: {
  mode: QueryMode
  route?: string
  onPick: (p: Place) => void
  selectedId?: string | null
  autoFocus?: boolean
}) {
  const id = useId()
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Result>({ kind: 'idle' })
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (autoFocus) input.current?.focus({ preventScroll: true })
  }, [autoFocus])

  useEffect(() => {
    const query = q.trim()
    if (query.length < MIN_CHARS) {
      setRes({ kind: 'idle' })
      return
    }
    const ctrl = new AbortController()
    setRes({ kind: 'loading' })
    const timer = window.setTimeout(() => {
      placesSearch({ q: query, mode, route }, { signal: ctrl.signal })
        .then(({ data }) => {
          const seen = new Set<string>()
          const places = [data.resolved, ...data.choices].filter((p): p is Place => {
            if (!p || p.stop_ids.length === 0 || seen.has(p.place_id)) return false
            seen.add(p.place_id)
            return true
          })
          setRes({ kind: 'ready', places, confirm: data.needs_confirm || places.length > 1 })
        })
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.error === 'aborted') return
          setRes({ kind: 'failed', error: e })
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      ctrl.abort()
    }
  }, [q, mode, route])

  const announce =
    res.kind === 'ready' ? (res.places.length ? (res.places.length === 1 ? t('off.search_result_one') : t('off.search_results', { count: res.places.length })) : t('wb_05_off.no_results')) : null

  return (
    <div className={styles.section}>
      <div className={styles.field}>
        <label htmlFor={id} className={styles.label}>
          {t('wb_05_off.search')}
        </label>
        <p className={styles.hint} id={`${id}-hint`}>
          {t('wb_05_off.search_hint')}
        </p>
        <div className={styles.searchBox}>
          <Icon name="search" className={styles.searchIcon} />
          <input
            ref={input}
            id={id}
            type="search"
            inputMode="search"
            enterKeyHint="search"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            className={styles.input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-describedby={`${id}-hint ${id}-status`}
          />
          {res.kind === 'loading' ? <span className={styles.searchSpin} role="status" aria-label={t('off.searching')} /> : null}
        </div>
      </div>
      <div id={`${id}-status`}>
        {res.kind === 'ready' && res.places.length === 0 ? <Note icon="search">{t('wb_05_off.no_results')}</Note> : null}
        {res.kind === 'failed' ? <Note>{errorMessage(res.error)}</Note> : null}
      </div>
      {res.kind === 'ready' && res.places.length > 0 ? (
        <>
          {res.confirm ? <p className={styles.sectionHeading}>{t('wb_05_off.confirm')}</p> : null}
          <ChoiceList
            label={t('wb_05_off.search')}
            notSure={false}
            selected={selectedId ?? null}
            onChoose={(v) => {
              const p = res.places.find((x) => x.place_id === v)
              if (p) onPick(p)
            }}
            choices={res.places.map((p) => ({ value: p.place_id, label: p.label, sublabel: <PlaceSub place={p} />, icon: placeIcon(p) }))}
          />
        </>
      ) : null}
      <LiveRegion text={announce} />
    </div>
  )
}
