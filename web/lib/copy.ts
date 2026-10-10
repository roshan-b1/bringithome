// Every string a rider sees, keyed by id. The ids and wording are PREP-REPORT.md section 10 (the reviewed copy,
// on-screen column), minus calm mode and wb_13 (cut Oct 9), plus the few lines ARCHITECTURE.md adds (decisions 8,
// 9, 11, 12, 16) and the app chrome. A Question's buttons are `<id>.<answer>`. scripts/copylint.mjs lints every
// value here; `npm run lint:copy` fails the check on a dash, a banned word or a promise we can't keep.
//
// Templates:
//   {slot}       filled by t(); slot names are the API's fact names (api/README "Card kind to copy key"), so a
//                card's `facts` fill its copy directly. PREP's {N} is {minutes} and {station} is {stop} for that
//                reason.
//   [ ... ]      an optional phrase, dropped whole when a slot inside it has no value. The API leaves a slot out
//                when it has nothing to say ("a missing value leaves the slot out, the copy drops its phrase").
// Phones, hours and counts are always slots, never literals (they come from contacts.json or the API).
// App copy says "meet it", never the pitch's other verb.

export const COPY = {
  // ---------------------------------------------------------------------------------------------- home and chrome
  'home.question': 'Left something on a MARTA bus or train?',
  'home.promise': 'Find the one you were on, see where it is now, and get a calm plan.',
  'home.cta': 'I lost something',
  'home.cta_hint': 'One question at a time.',
  'home.borrowed': 'No account, no app. Works on a borrowed phone, and nothing stays on it.',
  'home.step_1': 'Tap what you remember',
  'home.step_2': 'We match the bus or train you were on',
  'home.step_3': 'You get a calm plan, one step at a time',
  'home.steps_label': 'How it works',
  data_credit: "data from MARTA's public feeds",
  progress: 'Question {n} of about {m}',
  not_sure: 'Not sure',
  back: 'Back',
  continue: 'Continue',
  try_again: 'Try again',
  done: 'Done',
  clear_now: 'Clear now',
  cleared: 'Cleared. Nothing from this visit is left on this phone.',
  deletes_by: 'Your answers are deleted automatically by {time}.',
  loading: 'Loading',
  skip_to_content: 'Skip to content',

  // source chip and replay banner (6.1: a chip whenever data is not live; decision 13: the Replaying banner)
  'chip.live': 'Live',
  'chip.replay': 'Replay',
  'chip.schedule': 'Schedule only',
  'chip.live_label': 'Live data',
  'chip.replay_label': 'Recorded data, replayed',
  'chip.schedule_label': 'From the timetable, not a live position',
  'banner.replaying': 'Replaying {when}',
  'banner.replaying_hint': 'A recorded ride, matched the same way as a live one.',

  // errors (6.1: a 503 shows the report built from the rider's clues, plus a recorded example)
  'error.title': "That didn't load",
  'error.generic': 'Something went wrong on our side. Your answers are still here.',
  'error.offline': 'No connection right now. Your answers are still here.',
  'error.timeout': 'That took too long. Your answers are still here.',
  'error.db_unavailable': "Live data isn't answering right now. Here's a plan from your answers.",
  'error.not_found': 'That visit has expired. Visits are deleted within 24 hours.',
  'error.rate_limited': 'Too many tries at once. Wait a few seconds, then try again.',
  'error.see_recorded': 'See a recorded example',

  // ------------------------------------------------------------------------------- intake (PREP 10, voiced clips)
  wb_00_intro: 'We start with now and work back. Tap the closest answer. "Not sure" is always okay.',
  'wb_00_intro.start': 'Start',

  wb_01_what: 'What did you lose?',
  'wb_01_what.phone': 'Phone',
  'wb_01_what.wallet': 'Wallet, ID or Breeze card',
  'wb_01_what.bag': 'Bag or backpack',
  'wb_01_what.keys': 'Keys',
  'wb_01_what.laptop': 'Laptop or tablet',
  'wb_01_what.other': 'Something else',

  wb_02_now: 'Where are you now?',
  'wb_02_now.location': 'Use my location',
  'wb_02_now.still_there': 'Still where I got off',
  'wb_02_now.on_vehicle': 'On another bus or train',
  'wb_02_now.elsewhere': 'Somewhere else',
  'wb_02_now.location_hint': 'Used once to find stops near you. Never stored.',
  'wb_02_now.location_denied': "Location is off. That's okay, you can search for the stop instead.",

  wb_03_before: 'Picture the last time you had it. Where were you?',
  'wb_03_before.bus': 'On a bus',
  'wb_03_before.train': 'On a train',
  'wb_03_before.streetcar': 'On the streetcar',
  'wb_03_before.stop': 'At a stop or station',
  'wb_03_before.not_marta': 'Not on MARTA',

  wb_04_mode: 'Did you ride a bus, train or streetcar?',
  'wb_04_mode.bus': 'Bus',
  'wb_04_mode.rail': 'Train',
  'wb_04_mode.streetcar': 'Streetcar',
  'wb_04_mode.streetcar_hint': 'The streetcar has no live location, so we use its schedule.',

  wb_05_off: 'Where did you get off?',
  'wb_05_off.search': 'Search a stop or street',
  'wb_05_off.search_label': 'Stop, street or station',
  'wb_05_off.search_hint': 'Like "Peachtree Center" or "North Ave"',
  'wb_05_off.no_results': 'No stop matches that yet. Try a nearby street or station name.',
  'wb_05_off.away': '{distance} m away',
  'wb_05_off.confirm': 'Did you mean one of these?',

  wb_06_when: 'About when did you get off?',
  'wb_06_when.under_10': 'Under 10 min ago',
  'wb_06_when.10_30': '10 to 30 min ago',
  'wb_06_when.30_60': '30 to 60 min ago',
  'wb_06_when.1_2h': '1 to 2 hours ago',
  'wb_06_when.over_2h': 'Over 2 hours ago',
  'wb_06_when.pick': 'Pick a time',
  'wb_06_when.pick_label': 'About what time?',
  'wb_06_when.picked_yesterday': 'Got off around {time} last night ({day})?',

  wb_07_sure: 'How sure are you about the time?',
  'wb_07_sure.very': 'Very sure, within 5 min',
  'wb_07_sure.pretty': 'Pretty sure, within 15 min',
  'wb_07_sure.rough': 'Rough guess, within 30 min',

  wb_08_bus: 'Which bus?',
  'wb_08_bus.route': 'Route {route}',
  'wb_08_bus.type': 'Type a number',
  'wb_08_bus.type_label': 'Bus route number',

  wb_08_train: 'Which line?',
  'wb_08_train.RED': 'Red',
  'wb_08_train.GOLD': 'Gold',
  'wb_08_train.BLUE': 'Blue',
  'wb_08_train.GREEN': 'Green',

  wb_09_way: 'Which way was it going?',
  'wb_09_way.hint': 'The sign on the front showed where it was headed.',

  wb_10_car: 'Which part of the train?',
  'wb_10_car.front': 'Front car',
  'wb_10_car.middle': 'Middle',
  'wb_10_car.back': 'Back car',

  // decision 12: after wb_10, optional; fills Rail Car Number on the report, on the phone only
  car_number: 'Saw a car number inside?',
  'car_number.type': 'Type it',
  'car_number.no': 'No',
  'car_number.label': 'Car number',

  wb_11_taken: 'Do you think someone took it?',
  'wb_11_taken.hint': 'Any answer is okay.',
  'wb_11_taken.no': 'No, I left it',
  'wb_11_taken.maybe': 'Maybe',
  'wb_11_taken.yes': 'Yes, I think so',

  wb_12_safe: "Stay safe. Don't follow or confront anyone.",
  'wb_12_safe.call_police': 'Call MARTA Police {police_non_emergency}',
  'wb_12_safe.danger': 'In danger?',
  'wb_12_safe.call_911': 'Call {emergency_911}',

  // decision 9: "Which train are you on now?", when wb_02 was "On another bus or train"
  on_train: 'Which train are you on now?',
  'on_train.option': '{line} to {destination}[, next stop {next_stop}]',
  'on_train.none': "We can't see a train near you right now.",
  'on_train.not_train': "I'm not on a train",

  wb_14_done: 'Thanks. Looking for your bus or train now.',

  // decision 11: TellMe, at the expo table only; speech input is the browser's
  tell: "Tell us where and when. You'll pick what you lost on the next screen.",
  'tell.label': 'Where and when',
  'tell.mic': 'Speak',
  'tell.mic_stop': 'Stop',
  'tell.mic_disclosure': "Your browser's speech service hears this.",
  'tell.submit': 'Use this',
  'tell.or_tap': 'Or answer by tapping',

  // -------------------------------------------------------------------------------------------- results (PREP 10)
  res_01_found: 'Likely match: {vehicle}[, vehicle {label}][, train run {train_run}]. Now near {place}. How sure: {level}',
  'level.high': 'High',
  'level.medium': 'Medium',
  'level.low': 'Low',
  res_02_meet: "You can likely meet it at {stop} around {time}, in about {minutes} min. Walk there. If you miss it, there's still a plan.",
  res_03_tell_bus: 'When the bus pulls up, tell the driver what you lost and where you sat.',
  res_03_tell_train: 'Tell station staff what you lost and which car you were in. Stay back from the platform edge.',
  res_04_far: "Too far to meet right now. That's okay. Here's your plan.",
  res_05_nomatch: "We couldn't pin down the exact one yet. That's okay. Here's your plan.",
  res_notreporting: "That bus isn't sharing its location right now. Here's a plan based on its schedule.",
  res_canceled: "MARTA marked that trip as canceled. Here's your plan.",
  res_stale: 'Last seen {minutes} min ago.',
  // decision 8's other states
  'state.at_terminus': 'It is at the end of the line at {stop}. Staff there can check it.',
  'state.dark': 'This {vehicle_noun} stopped sharing its location {minutes} min ago. Report it now with the {vehicle_noun} number.',
  'state.replay_position': 'Replay: position at {time}',
  // decision 12 ContextLine; "One fits." only at High or Medium
  context_line: '{count} {counted} passed nearby.',
  'context_line.fits': 'One fits.',
  'map.text_heading': 'The map, in words',
  'map.unavailable': "The map can't load on this device. Everything it shows is listed here.",

  // ---------------------------------------------------------------------------------------------- plan (PREP 10)
  plan_step: 'Step {n} of {m}.',
  plan_01: 'One thing at a time.',
  plan_02: 'Keep going.',
  plan_03: 'Almost there.',
  plan_04: 'Last one.',
  plan_05_done: "That's the whole plan. You've done what you can for now.",

  bag_01_staff: "Bag on a train or platform? Tell staff it's yours right away. A bag left alone may be treated as a safety concern.",
  tracks_01: "If it fell onto the tracks, don't go after it. Tell station staff.",

  plan_body_1: 'Keep this: {vehicle}[, vehicle {label}][, train run {train_run}], {role} {stop} around {time}.',
  'role.alight': 'got off at',
  'role.board': 'got on at',
  borrowed_phone_hint: 'On a borrowed phone? Write it down.',
  'plan_body_1.show_staff': 'Show this screen to any MARTA staff.',

  plan_body_2_bus: 'Meet it at {stop} around {time}. Walk there. When the bus pulls up, tell the driver what you lost.',
  plan_body_2_train: 'Go to {stop}. Tell station staff what you lost and which car you were in.',
  'plan_body_2_train.on_train': 'Get off at {stop}. Tell station staff what you lost and which car you were in.',
  // decision 9 copy: the platform hint from the car answer, the direction, and the night line
  'meet.platform': 'Wait at the {platform_hint} of the platform, back from the edge.',
  'meet.toward': 'It will be heading to {toward}.',
  'meet.night': "If you're alone, ask station staff to help.",
  'meet.withdrawn': 'Too far to meet right now.',
  plan_body_2_none: 'Tell any MARTA staff now. Show them this screen.',
  'tell_staff.facts': '{vehicle}[, vehicle {label}][, train run {train_run}][, {direction}].',

  plan_body_3: 'Report it online: MARTA "Report a lost item".',
  'plan_body_3.paste': 'Paste these details:',
  'plan_body_3.reason': 'Reason: {form_reason}',
  'plan_body_3.item': 'Describe your item in the form yourself. We never send it anywhere.',
  // the form's own field labels (contacts.json lost_found_form.field_labels), plus the two PREP adds
  'form.route_number': 'Route Number',
  'form.bus_number': 'Bus Number',
  'form.direction': 'Direction of Travel/Destination',
  'form.location': 'Location',
  'form.time': 'Time',
  'form.rail_line': 'Rail Line',
  'form.rail_station': 'MARTA Rail Station',
  'form.rail_car_number': 'Rail Car Number',
  'form.rail_car_number_hint': 'Rail Car Number, if you saw it',
  'form.train_run': "MARTA train run {train_run}, from MARTA's public feeds",

  plan_body_4_weekday: 'Call Lost and Found at {lost_found_phone}, {lost_found_hours}. Pickup is by appointment. Items can take {lost_found_transit_time} to get there. They keep items {lost_found_hold}.',
  'plan_body_4_weekday.usually': 'It is usually open at this time, but today is not confirmed.',
  plan_body_4_weekend: 'Lost and Found is closed right now. It usually opens {next_weekday} at {opens_at}. Send the online report now, then call {lost_found_phone} when they open.',
  'plan_body_4_weekend.confirmed': 'Lost and Found is closed right now. It opens {next_weekday} at {opens_at}. Send the online report now, then call {lost_found_phone} when they open.',

  // decision 10's text (not in PREP 10 yet): no links, none are on the verified list
  freeze_cards: 'Freeze your cards now with your bank. When you call Lost and Found, ask which ID they accept. Claims are {lost_found_claim}.',
  phone_lost: 'Mark your phone as lost from this device, with its find service.',
  police_property: 'If MARTA Police picked it up, their property unit holds items {police_property_hold}. Call {police_property_unit}. Claims are {police_property_claims}.',
  safety_footer: 'In danger? Call {emergency_911}. To reach MARTA Police directly, call {police_emergency} or text {police_text}.',

  // card action buttons, by the API's label_id ({number} from contacts.json)
  'action.call_police': 'Call MARTA Police {number}',
  'action.call_911': 'Call {number}',
  'action.keep_copy': 'Copy',
  'action.report_open_form': 'Open form',
  'action.report_copy': 'Copy details',
  'action.call_lost_found': 'Call Lost and Found {number}',
  'action.email_lost_found': 'Email Lost and Found',
  'action.call_reminder': 'Add a reminder',
  'action.customer_service': 'Call MARTA customer service {number}',
  'action.route_schedule_info': 'Call MARTA route info {number}',
  'action.call_police_property': 'Call the property unit {number}',
  'action.copied': 'Copied',

  phone_borrowed: "Using a borrowed phone? That's okay. Nothing stays on this phone after you close this page.",
  'walk.slow': 'I walk slowly or use a mobility aid',

  // ------------------------------------------------------------------------------------------- somewhere else
  not_marta: "bring it home only covers MARTA buses, trains and the streetcar. Here's what else you can try.",
  'not_marta.ask': 'Where were you before you noticed? Start with the most recent place.',
  'not_marta.label': 'Place name',
  'not_marta.add': 'Add another place',
  place_call: 'Call {place}. Say what you lost, about when you were there, and how they can reach you.',
} as const satisfies Record<string, string>

export type CopyId = keyof typeof COPY
export type SlotValue = string | number

// Type-level template parsing: required slots are those outside [ ], optional ones inside.
type SlotsIn<S extends string> = S extends `${string}{${infer K}}${infer R}` ? K | SlotsIn<R> : never
type OutsideOpt<S extends string> = S extends `${infer A}[${string}]${infer B}` ? `${A}${OutsideOpt<B>}` : S
type InsideOpt<S extends string> = S extends `${string}[${infer M}]${infer B}` ? M | InsideOpt<B> : never
type Required_<K extends CopyId> = SlotsIn<OutsideOpt<(typeof COPY)[K]>>
type Optional_<K extends CopyId> = Exclude<SlotsIn<InsideOpt<(typeof COPY)[K]>>, Required_<K>>
export type SlotsFor<K extends CopyId> = { [S in Required_<K>]: SlotValue } & { [S in Optional_<K>]?: SlotValue | null }
type SlotArgs<K extends CopyId> = [Required_<K> | Optional_<K>] extends [never] ? [] : [slots: SlotsFor<K>]

const SLOT = /\{([a-z0-9_]+)\}/gi
const OPTIONAL = /\[([^\]]*)\]/g

function render(template: string, slots: Readonly<Record<string, SlotValue | null | undefined>>, id: string): string {
  const has = (k: string) => slots[k] !== undefined && slots[k] !== null && slots[k] !== ''
  const withOptional = template.replace(OPTIONAL, (_m, inner: string) =>
    [...inner.matchAll(SLOT)].every((m) => has(m[1]!)) ? inner : '',
  )
  return withOptional.replace(SLOT, (_m, k: string) => {
    if (!has(k)) throw new Error(`copy ${id}: no value for {${k}}`)
    return String(slots[k])
  })
}

/** The string for `id`, its slots filled. Typed: a required slot can't be left out, an unknown one can't be passed. */
export function t<K extends CopyId>(id: K, ...args: SlotArgs<K>): string {
  return render(COPY[id], (args[0] ?? {}) as Record<string, SlotValue | null | undefined>, id)
}

/**
 * Loose fill for slot values that arrive untyped (a plan card's `facts`): returns null instead of throwing when a
 * required slot is missing, so a card with an incomplete fact set drops that line instead of showing "{stop}".
 */
export function tFacts(id: CopyId, facts: Readonly<Record<string, SlotValue | null | undefined>>): string | null {
  try {
    return render(COPY[id], facts, id)
  } catch {
    return null
  }
}

export type TemplatePart = { text: string } | { slot: string }

/**
 * A template split into text and slots, optional phrases resolved against `present` (the slots that have a value).
 * For rendering slots as elements (a phone number as a tel: link) instead of plain text; see components/ui/Copy.
 */
export function templateParts(id: CopyId, present: ReadonlySet<string>): TemplatePart[] {
  const template = COPY[id].replace(OPTIONAL, (_m, inner: string) =>
    [...inner.matchAll(SLOT)].every((m) => present.has(m[1]!)) ? inner : '',
  )
  const out: TemplatePart[] = []
  let at = 0
  for (const m of template.matchAll(SLOT)) {
    if (m.index > at) out.push({ text: template.slice(at, m.index) })
    out.push({ slot: m[1]! })
    at = m.index + m[0].length
  }
  if (at < template.length) out.push({ text: template.slice(at) })
  return out
}

/** The slot names a template uses (for tests and the lint). */
export function slotsOf(id: CopyId): string[] {
  return [...COPY[id].matchAll(SLOT)].map((m) => m[1]!)
}

/** "Step 2 of 5. Keep going." (PREP plan_01..04, generalized to the plan's real length). */
export function planStepLine(n: number, m: number): string {
  const cheer: CopyId = n === 1 ? 'plan_01' : n === m ? 'plan_04' : n === m - 1 ? 'plan_03' : 'plan_02'
  return `${t('plan_step', { n, m })} ${t(cheer)}`
}
