#!/usr/bin/env node
// The copy lint (PREP-REPORT.md section 10 "Build-time lint", ARCHITECTURE.md decision 12): one check for every
// string a rider sees. A JavaScript port of api/backtrack_api/copylint.py, rule for rule, so the server's templates
// and the phone's copy are held to the same line. Why a lint and not a style note: telling a panicked rider to run,
// hurry or hold the doors can get them hurt, and a promise that MARTA will hold a vehicle is one we can't keep.
//
// Flags:
//   dash         em dash, en dash and their look-alikes, and "--"
//   banned_word  run, hurry, rush, door, chase, jump, confront, follow, catch, therapy, treatment (and their
//                inflections) and the two-letter abbreviation for artificial intelligence, as whole words; plus
//                "cross the tracks"
//   promise      "hold the bus", "contact the driver", "wait for you" and the like
//   old_name     the product's old name, which no rider-facing string may use any more
// Allows (the prep allowlist):
//   - negated safety lines: a banned word whose clause, walked back past only connectors, reaches a negator
//     ("don't follow", "Don't follow or confront anyone", "don't go after it" in tracks_01)
//   - "train run" / "train runs" (the report's run-number line) and "running on Vultr" (the proof footer)
//
// Usage:
//   node scripts/copylint.mjs                 lint lib/copy.ts (every value), plus the New York time check
//   node scripts/copylint.mjs --text a.txt -  also lint rendered page text from files or stdin ("-")
// Exit code 1 on any problem. `lint()` is exported for the e2e test, which lints the rendered page text.

const DASHES = '‒–—―⸺⸻︱︲﹘'
const DASH = new RegExp(`[${DASHES}]|--`, 'g')

export const BANNED_WORDS = {
  run: ['run', 'runs', 'running', 'ran'],
  hurry: ['hurry', 'hurries', 'hurried', 'hurrying'],
  rush: ['rush', 'rushes', 'rushed', 'rushing'],
  door: ['door', 'doors'],
  chase: ['chase', 'chases', 'chased', 'chasing'],
  jump: ['jump', 'jumps', 'jumped', 'jumping'],
  confront: ['confront', 'confronts', 'confronted', 'confronting', 'confrontation'],
  follow: ['follow', 'follows', 'followed', 'following'],
  catch: ['catch', 'catches', 'catching', 'caught'],
  therapy: ['therapy', 'therapies'],
  treatment: ['treatment', 'treatments'],
}
const FORMS = [...new Set(Object.values(BANNED_WORDS).flat())].sort((a, b) => b.length - a.length)
const BANNED = new RegExp(`\\b(?:${FORMS.join('|')})\\b`, 'gi')
const BANNED_WHOLE = new RegExp(`^(?:${FORMS.join('|')})$`, 'i')
// the abbreviation, with or without dots ("AI", "A.I.", "AI-powered"); never inside a word ("aisle", "Hawaii")
const ABBREVIATION = /(?<![A-Za-z0-9])A\.?I(?![A-Za-z0-9])\.?/gi
const CROSS_TRACKS = /\bcross(?:es|ed|ing)?\s+(?:over\s+)?(?:the\s+)?(?:train\s+)?tracks?\b/gi
const PROMISES = [
  /\bhold(?:s|ing)?\s+(?:the|this|that|your|a|their)\s+(?:bus|train|vehicle|car|streetcar)\b/gi,
  /\b(?:contact|contacts|contacted|contacting|radio|radios|radioed|radioing|call|calls|calling|notify|notifies|notified|notifying|alert|alerts|alerting)\s+(?:the|your|a|their)\s+(?:bus\s+|train\s+)?(?:driver|operator)s?\b/gi,
  /\bwait(?:s|ing)?\s+for\s+you\b/gi,
]
const OLD_NAME = /\bback\s?track\b/gi
const ALLOW_PHRASES = [/\btrain\s+runs?\b/gi, /\brunning\s+on\s+Vultr\b/gi]
const NEGATORS = new Set(['no', 'not', 'never', 'nor', 'cannot'])
// words that may sit between a negator and the banned word without breaking the negation
const CONNECTORS = new Set(['or', 'and', 'to', 'try', 'the', 'a', 'any', 'anyone', 'ever', 'hold', 'have', 'need',
  'go', 'for', 'after', 'it', 'them', 'him', 'her', 'will', 'can', 'be', 'able'])
// a comma ends the negation too: "Don't wait, run" is an instruction to run
const CLAUSE_END = /[.!?;:,\n]/g
const TOKEN = /[A-Za-z']+/g

const isNegator = (w) => NEGATORS.has(w) || w.endsWith("n't") || w === 'dont'

function negated(text, start) {
  let clauseStart = 0
  for (const m of text.slice(0, start).matchAll(CLAUSE_END)) clauseStart = m.index + 1
  const words = text.slice(clauseStart, start).match(TOKEN) ?? []
  for (const word of words.reverse()) {
    const w = word.toLowerCase().replace(/^'+|'+$/g, '')
    if (isNegator(w)) return true
    if (CONNECTORS.has(w) || BANNED_WHOLE.test(w)) continue
    return false
  }
  return false
}

const spans = (text, rxs) => rxs.flatMap((rx) => [...text.matchAll(rx)].map((m) => [m.index, m.index + m[0].length]))
const inside = ([a, b], list) => list.some(([x, y]) => x <= a && b <= y)

/** Every rule `text` breaks, by position. An empty list means it's clean. */
export function lint(raw) {
  const text = raw.replace(/[‘’]/g, "'") // curly apostrophes: "don’t" is "don't"
  const problems = [...text.matchAll(DASH)].map((m) => ({ kind: 'dash', text: m[0], at: m.index }))
  const allowed = spans(text, ALLOW_PHRASES)
  for (const m of text.matchAll(BANNED)) {
    if (!inside([m.index, m.index + m[0].length], allowed) && !negated(text, m.index))
      problems.push({ kind: 'banned_word', text: m[0], at: m.index })
  }
  for (const m of text.matchAll(ABBREVIATION)) problems.push({ kind: 'banned_word', text: m[0], at: m.index })
  for (const m of text.matchAll(CROSS_TRACKS))
    if (!negated(text, m.index)) problems.push({ kind: 'banned_word', text: m[0], at: m.index })
  for (const rx of PROMISES)
    for (const m of text.matchAll(rx))
      if (!negated(text, m.index)) problems.push({ kind: 'promise', text: m[0], at: m.index })
  for (const m of text.matchAll(OLD_NAME)) problems.push({ kind: 'old_name', text: m[0], at: m.index })
  return problems.sort((a, b) => a.at - b.at)
}

/** Lint a table (id -> text); only the ids with problems come back. */
export function lintAll(table) {
  const out = {}
  for (const [id, text] of Object.entries(table)) {
    const p = lint(text)
    if (p.length) out[id] = p
  }
  return out
}

const fmt = (where, problems) => problems.map((p) => `  ${where}: ${p.kind} ${JSON.stringify(p.text)} at ${p.at}`)

async function main(argv) {
  const { COPY } = await import('../lib/copy.ts')
  const { clock, dayAndClock } = await import('../lib/time.ts')
  const out = []
  for (const [id, problems] of Object.entries(lintAll(COPY))) out.push(...fmt(`copy ${id}`, problems))

  // decision 6: one fixed instant must format as New York time whatever the machine's zone
  const instant = '2026-10-10T19:47:10Z' // Sat Oct 10, 3:47 PM EDT
  if (clock(instant) !== '3:47 PM' || dayAndClock(instant) !== 'Sat Oct 10, 3:47 PM')
    out.push(`  time: ${instant} formatted as ${dayAndClock(instant)}, expected Sat Oct 10, 3:47 PM (New York)`)

  const ti = argv.indexOf('--text')
  if (ti >= 0) {
    const { readFileSync } = await import('node:fs')
    for (const f of argv.slice(ti + 1)) {
      const text = f === '-' ? readFileSync(0, 'utf8') : readFileSync(f, 'utf8')
      out.push(...fmt(f === '-' ? 'stdin' : f, lint(text)))
    }
  }
  if (out.length) {
    console.error(`copy lint: ${out.length} problem(s)\n${out.join('\n')}`)
    process.exit(1)
  }
  console.log(`copy lint: ${Object.keys(COPY).length} strings clean, New York time check ok`)
}

if (import.meta.url === `file://${process.argv[1]}`) await main(process.argv.slice(2))
