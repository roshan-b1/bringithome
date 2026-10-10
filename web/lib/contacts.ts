// The verified MARTA contact list, bundled (decision 12), so the safety footer and the report render with no
// server. lib/contacts.json is a copy of api/backtrack_api/data/contacts.json made by scripts/sync_contacts.mjs;
// every value there traces to PREP-REPORT section 6 with source_urls and verified_at. Phones are never literals
// in copy.ts: they come from here or from a plan card's facts.

import data from './contacts.json'

interface Entry {
  kind: string
  value: string | null
  tel?: string
  sms?: string
  mailto?: string
  url?: string
  status?: string
  show_in_app?: boolean
  field_labels?: Record<string, string>
}

const entries = data.entries as unknown as Record<string, Entry>

export interface Contact {
  key: string
  value: string // as printed: "404-848-4911"
  href: string | null // tel:, sms:, mailto: or https:
}

/** A contact the app may show, or null (missing, no value, or held back until confirmed, like the property unit). */
export function contact(key: string): Contact | null {
  const e = entries[key]
  if (!e || e.value === null || e.show_in_app === false) return null
  return { key, value: e.value, href: e.tel ?? e.sms ?? e.mailto ?? e.url ?? null }
}

function must(key: string): Contact {
  const c = contact(key)
  if (!c) throw new Error(`contacts.json has no usable ${key}`)
  return c
}

/** safety_footer's slots and links: 911, MARTA Police's emergency line and its text line. */
export function safetyContacts(): { emergency_911: Contact; police_emergency: Contact; police_text: Contact } {
  return { emergency_911: must('emergency_911'), police_emergency: must('police_emergency'), police_text: must('police_text') }
}

/** The printed number behind an action's href ("tel:+14048484900" -> "404-848-4900"), for {number} in labels. */
export function displayForHref(href: string): string | null {
  for (const e of Object.values(entries)) {
    if (e.value && e.show_in_app !== false && [e.tel, e.sms, e.mailto, e.url].includes(href)) return e.value
  }
  return null
}

/** MARTA's "Report a lost item" form: its URL and its own field labels. */
export function lostFoundForm(): { url: string; labels: Record<string, string> } {
  const e = entries.lost_found_form
  return { url: e?.url ?? '', labels: e?.field_labels ?? {} }
}
