// Every time a rider sees is New York wall-clock time, formatted with Intl and timeZone America/New_York, never the
// device zone (ARCHITECTURE.md decision 6). The strings match the API's own (report.py rider_clock, rider_day,
// rider_date), so a time on a card and the same time in the copied report read identically. No date library.

export const TZ = 'America/New_York'

const parts = new Intl.DateTimeFormat('en-US', {
  timeZone: TZ,
  hourCycle: 'h23',
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
  hour: 'numeric',
  minute: 'numeric',
  second: 'numeric',
  weekday: 'short',
})
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const WEEKDAYS: Record<string, string> = { Sun: 'Sunday', Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday' }

export interface NyParts {
  year: number
  month: number // 1..12
  day: number
  hour: number // 0..23
  minute: number
  second: number
  weekday: string // "Fri"
}

export const toDate = (t: Date | string): Date => (typeof t === 'string' ? new Date(t) : t)

/** The New York wall clock at instant `t`. */
export function nyParts(t: Date | string): NyParts {
  const out: Record<string, string> = {}
  for (const p of parts.formatToParts(toDate(t))) out[p.type] = p.value
  return {
    year: Number(out.year),
    month: Number(out.month),
    day: Number(out.day),
    hour: Number(out.hour) % 24,
    minute: Number(out.minute),
    second: Number(out.second),
    weekday: out.weekday ?? '',
  }
}

/** New York's offset from UTC at instant `t`, in minutes (-240 in EDT, -300 in EST). */
export function nyOffsetMinutes(t: Date | string): number {
  const d = toDate(t)
  const p = nyParts(d)
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)
  return Math.round((asUtc - Math.floor(d.getTime() / 1000) * 1000) / 60000)
}

/** The instant a New York wall-clock time names. Two passes settle the offset across a DST change. */
export function nyWallToInstant(year: number, month: number, day: number, hour: number, minute: number): Date {
  const wall = Date.UTC(year, month - 1, day, hour, minute)
  let t = wall - nyOffsetMinutes(new Date(wall)) * 60000
  t = wall - nyOffsetMinutes(new Date(t)) * 60000
  return new Date(t)
}

/** "3:47 PM", "12:05 AM": no leading zero, 12-hour, cut to the minute (report.py rider_clock). */
export function clock(t: Date | string): string {
  const p = nyParts(t)
  return `${p.hour % 12 || 12}:${String(p.minute).padStart(2, '0')} ${p.hour < 12 ? 'AM' : 'PM'}`
}

/** "Fri Oct 9" (report.py rider_day). */
export function shortDay(t: Date | string): string {
  const p = nyParts(t)
  return `${p.weekday} ${MONTHS[p.month - 1]!.slice(0, 3)} ${p.day}`
}

/** "Friday, October 9", for prose (report.py rider_date). */
export function longDate(t: Date | string): string {
  const p = nyParts(t)
  return `${WEEKDAYS[p.weekday] ?? p.weekday}, ${MONTHS[p.month - 1]} ${p.day}`
}

/** "Fri Oct 9, 3:27 PM": the Replaying banner and anything that may not be today. */
export function dayAndClock(t: Date | string): string {
  return `${shortDay(t)}, ${clock(t)}`
}

/** Same New York calendar day? */
export function sameNyDay(a: Date | string, b: Date | string): boolean {
  const p = nyParts(a), q = nyParts(b)
  return p.year === q.year && p.month === q.month && p.day === q.day
}

/** Whole minutes from `from` to `to`, floored (a bus 4 min 50 s away is "4 min", never rounded up). */
export function minutesBetween(from: Date | string, to: Date | string): number {
  return Math.floor((toDate(to).getTime() - toDate(from).getTime()) / 60000)
}

/**
 * A picked "HH:MM" (24-hour, New York) as an instant: today's date at that time, rolled back 24 h (never 12) if
 * it would be in the future (decision 6, matching red team: a picked 11:50 PM at 12:20 AM is last night).
 */
export function resolvePickedTime(hhmm: string, now: Date): Date {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim())
  if (!m) throw new Error(`not a time: ${hhmm}`)
  const hour = Number(m[1]), minute = Number(m[2])
  if (hour > 23 || minute > 59) throw new Error(`not a time: ${hhmm}`)
  const p = nyParts(now)
  let at = nyWallToInstant(p.year, p.month, p.day, hour, minute)
  if (at.getTime() > now.getTime()) {
    const y = new Date(Date.UTC(p.year, p.month - 1, p.day) - 86400000)
    at = nyWallToInstant(y.getUTCFullYear(), y.getUTCMonth() + 1, y.getUTCDate(), hour, minute)
  }
  return at
}

/** ISO 8601 with New York's offset, the way the API writes times: "2026-10-09T15:47:00-04:00". */
export function toNyIso(t: Date | string): string {
  const d = toDate(t)
  const p = nyParts(d)
  const off = nyOffsetMinutes(d)
  const sign = off < 0 ? '-' : '+'
  const abs = Math.abs(off)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  )
}
