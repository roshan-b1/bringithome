// Every distance a rider sees, in US units. The API keeps sending metres (distance_m, radius_m, accuracy_m); the app
// converts only here, at display time, and every other piece of arithmetic (walk times, the meet rule, the map's
// circles) stays in metres.
//
//   under 0.2 mi    feet, to the nearest 5 ft below 100 ft and to the nearest 50 ft from there ("15 ft", "350 ft",
//                   "1,000 ft"); a phone fix is no better than that
//   0.2 to 10 mi    miles, to the nearest 0.05 below 1 mi and to 0.1 from 1 mi, trailing zeros dropped ("0.25 mi",
//                   "0.3 mi", "1.2 mi")
//   10 mi and up    whole miles ("12 mi")
//
// The number and its unit are joined by a regular space, so copy, tests and the copy lint read plain text; where a
// distance can wrap on screen, NoBreak (components/plan/FactText.tsx) keeps "350 ft" together with keepDistance().

const M_PER_FT = 0.3048
const FT_PER_MI = 5280
const FEET_BELOW_MI = 0.2

const grouped = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

/** "0 ft", "15 ft", "350 ft", "1,000 ft", "0.25 mi", "1.2 mi", "12 mi"; '' for a value that isn't a number. */
export function formatDistance(m: number): string {
  if (!Number.isFinite(m)) return ''
  const ft = Math.max(0, m) / M_PER_FT
  const mi = ft / FT_PER_MI
  if (mi < FEET_BELOW_MI) {
    const step = ft < 100 ? 5 : 50
    return `${grouped.format(Math.round(ft / step) * step)} ft`
  }
  if (mi < 10) {
    // in hundredths, so 0.25 never prints as 0.25000000000000006
    const hundredths = mi < 1 ? Math.round(mi * 20) * 5 : Math.round(mi * 10) * 10
    return `${grouped.format(hundredths / 100)} mi`
  }
  return `${grouped.format(Math.round(mi))} mi`
}

// a number (with its thousands comma or decimals) followed by a space and a distance unit
const DISTANCE = /(\d) (ft|mi)\b/g

/** The same text with each "350 ft" or "1.2 mi" joined by a no-break space, so the unit never starts a line. */
export const keepDistance = (text: string): string => text.replace(DISTANCE, '$1 $2')
