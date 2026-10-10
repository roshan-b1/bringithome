'use client'

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'

// Speech input for TellMe through the browser's own Web Speech API (decision 14: no server speech-to-text). The
// browser's speech service hears the audio, which is why the screen shows that disclosure next to the mic. The mic
// is hidden when the API is missing, when the browser refused the microphone, and offline (the service is remote).
// Nothing is recorded or kept: each result goes straight into the textarea, in memory.

interface Alternative {
  transcript: string
}
interface Result {
  readonly isFinal: boolean
  readonly length: number
  [i: number]: Alternative
}
interface ResultEvent {
  readonly resultIndex: number
  readonly results: { readonly length: number; [i: number]: Result }
}
interface Recognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  onresult: ((e: ResultEvent) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}
type RecognitionCtor = new () => Recognition

function ctor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

const noop = () => () => {}

export interface Speech {
  supported: boolean
  listening: boolean
  start: () => void
  stop: () => void
}

/** `onText(text)` gets the whole utterance so far (interim results included), replacing the previous one. */
export function useSpeech(onText: (text: string) => void): Speech {
  // read after hydration only: the server render has no window, and a mismatch would flash the mic
  const available = useSyncExternalStore(noop, () => ctor() !== null, () => false)
  const [denied, setDenied] = useState(false)
  const [listening, setListening] = useState(false)
  const rec = useRef<Recognition | null>(null)
  const latest = useRef(onText)
  useEffect(() => {
    latest.current = onText
  })

  const stop = useCallback(() => {
    rec.current?.stop()
  }, [])

  const start = useCallback(() => {
    const C = ctor()
    if (!C || rec.current) return
    const r = new C()
    r.lang = 'en-US'
    r.continuous = false
    r.interimResults = true
    r.maxAlternatives = 1
    r.onresult = (e) => {
      let text = ''
      for (let i = 0; i < e.results.length; i++) text += e.results[i]?.[0]?.transcript ?? ''
      latest.current(text.trim())
    }
    r.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') setDenied(true)
    }
    r.onend = () => {
      rec.current = null
      setListening(false)
    }
    rec.current = r
    setListening(true)
    try {
      r.start()
    } catch {
      rec.current = null
      setListening(false)
    }
  }, [])

  useEffect(() => () => rec.current?.abort(), [])

  return { supported: available && !denied, listening, start, stop }
}
