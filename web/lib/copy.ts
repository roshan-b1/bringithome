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
  'home.borrowed': 'No account, no app. Works on a borrowed phone. Nothing stays on it after you close this page.',
  'home.step_1': 'Tap what you remember',
  'home.step_2': 'We match the bus or train you were on',
  'home.step_3': 'You get a calm plan, one step at a time',
  'home.steps_label': 'How it works',
  data_credit: "data from MARTA's public feeds",
  progress: 'Question {n} of {m}',
  not_sure: 'Not sure',
  back: 'Back',
  continue: 'Continue',
  try_again: 'Try again',
  done: 'Done',
  clear_now: 'Clear now',
  cleared: 'Cleared. Nothing from this visit is left on this phone.',
  deletes_by: 'Your answers delete themselves 2 hours after you stop using this page.',
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
  'error.not_found': 'That visit has expired. Visits are deleted 2 hours after they were last used.',
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
  'wb_05_off.away': '{distance} away',
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
  'safety.call_police': 'Call MARTA Police',
  'safety.text_police': 'Text MARTA Police',

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
  'action.directions': 'Walking directions',
  'action.copied': 'Copied',

  phone_borrowed: "Using a borrowed phone? That's okay. Nothing stays on this phone after you close this page.",
  'walk.slow': 'I walk slowly or use a mobility aid',

  // ------------------------------------------------------------------------------------------- somewhere else
  not_marta: "bring it home only covers MARTA buses, trains and the streetcar. Here's what else you can try.",
  'not_marta.ask': 'Where were you before you noticed? Start with the most recent place.',
  'not_marta.label': 'Place name',
  'not_marta.add': 'Add another place',
  place_call: 'Call {place}. Say what you lost, about when you were there, and how they can reach you.',

  // ===== Intake screens (components/intake): the four questions, TellMe, the no-stop steps and the somewhere-else path. Not in PREP section 10: for team review.
  // shared
  'intake.type_instead': 'Type it instead',
  'intake.not_marta_hint': 'A store, a cafe, a rideshare or anywhere else',

  // Bus or train? (the team's wording for question 2; wb_04_mode stays the long form)
  'mode.title': 'Bus or train?',
  'mode.lead': 'More than one kind of ride stops at {place}.',

  // Where did you get off?
  // "sent once": the results and live maps show the rider's own dot later, but on the phone only; this lookup is
  // the one time a position leaves it (plus "Which train are you on now?" on the plan)
  'off.say_title': 'Say it in one sentence',
  'off.say_example': 'Like "I left my backpack on the Red Line, got off at West End 5 min ago"',
  'off.somewhere_else': 'Lost it somewhere else?',
  'when.just_now': 'Just now',
  'when.just_now_hint': 'In the last 10 minutes',
  'when.earlier': 'Earlier',
  'plan.item_hint': 'Optional. It adds the steps for that item.',
  'plan.item.phone': 'Phone',
  'plan.item.wallet': 'Wallet or ID',
  'plan.item.bag': 'Bag',
  'plan.item.keys': 'Keys',
  'plan.item.laptop': 'Laptop',
  'plan.item.other': 'Other',
  'off.locating': 'Finding stops near you. Your location is sent once for this and never stored.',
  'off.near_heading': 'Near you now',
  'off.recorded_heading': 'From the recorded ride',
  'off.recorded_found': '{count} places from the recorded ride.',
  'off.recorded_found_one': 'One place from the recorded ride.',
  'off.near_found': '{count} places near you.',
  'off.near_found_one': 'One place near you.',
  'off.near_none': "We didn't find a stop near you. Search for it instead.",
  'off.near_failed': "We couldn't load stops near you. Search for it instead.",
  'off.location_unavailable': "We couldn't get your location. That's okay, you can search for the stop instead.",
  'off.use_location': 'Use my location',
  'off.routes': 'Routes {routes}',
  'off.routes_more': 'Routes {routes} and {more} more',
  'off.your_answer': 'Your answer',
  'off.search_results': '{count} matches.',
  'off.search_result_one': 'One match.',
  'off.searching': 'Searching',

  // About when did you get off?
  'when.use_time': 'Use {time}',
  'when.picked_yesterday_day': 'Got off around {time} yesterday ({day})?',

  // done, when there is no stop to match against: no session can be made, so the first steps come from the bundled
  // copy and contacts, and the stop can still be added
  'done.no_place_title': 'We need a stop to find the exact one',
  'done.no_place_lead': "Add where you got off and we'll look again. You can start on these steps now.",
  'done.add_stop': 'Add the stop',
  'done.staff_title': 'Tell staff',
  'done.staff': 'Tell any MARTA driver or station staff what you lost and about when.',
  'done.report_title': 'Report it online',
  'done.call_title': 'Call Lost and Found',

  // TellMe
  'tell.title': 'Tell us where and when',
  'tell.lead': 'What you lost, where you got off, and about when.',
  'tell.example': 'Like: got off the 49 near Peachtree Center around 1:15',
  'tell.listening': 'Listening. Say where and when.',
  'tell.reading': 'Reading that',
  'tell.confirm_title': 'Is this right?',
  'tell.confirm_lead': 'Nothing is used until you tap yes.',
  'tell.what': 'What you lost',
  'tell.ride': 'What you rode',
  'tell.where_off': 'Where you got off',
  'tell.where_on': 'Where you got on',
  'tell.when': 'When',
  'tell.you_said': 'You said "{phrase}"',
  'tell.pick_one': 'Tap the one you meant.',
  'tell.none_of_these': 'None of these',
  'tell.mode_bus': 'Bus[, route {route}]',
  'tell.mode_rail': 'Train',
  'tell.mode_streetcar': 'Streetcar',
  'tell.time': 'About {time}[ {day}]',
  'tell.yes': "Yes, that's right",
  'tell.again': 'Type it again',
  'tell.asked_next': "We'll ask for anything you leave out.",
  'tell.nothing': "We couldn't pick out a stop or a time from that. Try other words, or answer by tapping.",
  'tell.unavailable': "Typing it isn't working right now. Let's answer by tapping instead.",
  'tell.answer_by_tapping': 'Answer by tapping',

  // somewhere else
  'elsewhere.title': 'Where were you before you noticed?',
  'elsewhere.lead': 'Start with the most recent place. Add up to 3.',
  'elsewhere.add': 'Add',
  'elsewhere.quick': 'Or tap one',
  'elsewhere.pick.store': 'the store',
  'elsewhere.pick.restaurant': 'the restaurant or cafe',
  'elsewhere.pick.work': 'work or school',
  'elsewhere.pick.ride': 'the rideshare or taxi company',
  'elsewhere.list': 'Your places, most recent first',
  'elsewhere.most_recent': 'Most recent',
  'elsewhere.remove': 'Remove {place}',
  'elsewhere.full': "That's 3 places. Remove one to add another.",
  'elsewhere.see_plan': 'See what to do',
  'elsewhere.not_sure_place': 'the places you were before you noticed',
  'elsewhere.plan_title': "Here's what you can try",
  'elsewhere.plan_lead': 'Start with the most recent place.',
  'elsewhere.freeze': 'Freeze your cards now with your bank.',
  'elsewhere.what_lead': 'Optional. A phone or a wallet gets its own step in your plan.',
  'elsewhere.what_skip': 'Skip',
  'elsewhere.start_over': 'Start over',

  // ===== Results screens (components/results): the match, the one follow-up question, where it is now. Not in PREP section 10: for team review.
  // ------------------------------------------------------------------------------------------- headline, context
  'results.found': 'Likely match: {vehicle}[, vehicle {label}][, train run {train_run}]',
  // the same headline in two parts, so the vehicle can be the big line (the h1 still reads as one sentence)
  'results.found_lead': 'Likely match:',
  'results.found_name': '{vehicle}[, vehicle {label}][, train run {train_run}]',
  'results.how_sure': 'How sure: {level}',
  'results.see_plan': 'See your plan',
  'results.change_answers': 'Change my answers',
  'results.looking': 'Looking for your bus or train',
  // the second sentences of res_05_nomatch and res_notreporting, as leads under their first sentence
  'results.nomatch_title': "We couldn't pin down the exact one yet.",
  'results.nomatch_lead': "That's okay. Here's your plan.",
  'results.notreporting_title': "That bus isn't sharing its location right now.",
  'results.notreporting_title_train': "That train isn't sharing its location right now.",
  'results.notreporting_lead': "Here's a plan based on its schedule.",
  // decision 7's streetcar line (the streetcar has no live data and always takes the schedule path)
  'results.streetcar_title': "The streetcar doesn't share its location.",
  'results.streetcar_lead': "Here's a plan from its schedule.",
  // ContextLine's second line: decision 7's "within 400 m of {place} between {T1} and {T2}", shortened; every
  // distance slot takes formatDistance()'s text ("0.25 mi", "350 ft"), never metres
  'context.where': 'Within {radius} of {place}, {from} to {to}.',
  'context.none': 'No {counted} passed within {radius} of {place} from {from} to {to}.',
  'context.none_window': 'No {counted} passed within {radius} of your stop from {from} to {to}.',
  'context.none_bare': 'No {counted} passed within {radius} of your stop.',

  // the vehicle, named the way the plan's cards name it ("Bus 49", "Red Line train")
  'vehicle.bus': 'Bus {route}',
  'vehicle.rail': '{line} train',
  'vehicle.rail_unknown': 'Train',
  'vehicle.streetcar': 'Streetcar',
  'noun.bus': 'bus',
  'noun.buses': 'buses',
  'noun.train': 'train',
  'noun.trains': 'trains',
  'noun.either': 'bus or train',
  'noun.both': 'buses and trains',
  'route.badge': 'Route {route}',

  // ----------------------------------------------------------------------------------- the one question (refine)
  // ambiguous: the tie-break as one question
  'tie.title': 'Which one were you on?',
  'tie.lead': 'Two fit what you remembered. Tap yours.',
  'tie.option': '{vehicle}[, {direction}]',
  'tie.not_sure': 'Not sure, plan for both',
  // a tie the re-run couldn't split (or "Not sure"): both shown, the plan covers both (api/README "Statuses")
  'tie.open_title': 'Two fit what you remembered.',
  'tie.open_lead': 'Your plan covers both.',
  // uncertain: separate_by as one question (route and direction reuse wb_08_bus and wb_09_way)
  'separate.lead': 'One more tap can tell them apart.',
  'separate.board_place': 'Where did you get on?',
  'separate.not_sure': 'Not sure, plan without it',
  'separate.fits_heading': 'Closest fits',
  'separate.times': 'Passed your stop at {times}',
  'separate.or': ' or ',
  'refine.checking': 'Checking again with your answer',
  'refine.failed': "We couldn't check again just now. Tap your answer to try again.",

  // --------------------------------------------------------------------------------------------- candidate card
  'candidate.passed': 'Passed {distance} from {place} at {time}',
  'candidate.passed_near': 'Passed nearby at {time}',
  'candidate.continuing': 'Route {route}, continuing as {continuing_as}',
  'candidate.label_bus': 'Vehicle {label}',
  'candidate.train_run': 'Train run {train_run}',
  'candidate.level_aria': 'How sure: {level}',
  'flag.route_differs': 'Showed a different route than your answer',
  'flag.line_differs': 'A different line than your answer',
  'flag.direction_differs': 'Going the other way from your answer',
  'flag.off_shape': 'Was off its usual route',
  'flag.feed_canceled_trip': "MARTA's feed listed this trip as canceled",
  'flag.relies_on_gap': 'Its location had a gap around then',

  // ------------------------------------------------------------------------------------- not_reporting schedule
  'schedule.heading': 'From the timetable',
  'schedule.trip': 'Route {route}[ to {headsign}]',
  'schedule.trip_named': '{vehicle}[ to {headsign}]',
  'schedule.at': 'Due at {stop} at {time}',
  'schedule.block_now': 'Now: bus {label}, {note}.',
  'schedule.same_bus': 'probably the same bus',
  'schedule.canceled': 'MARTA marked this trip as canceled.',

  // ------------------------------------------------------------------------------------------------ LiveVehicle
  'now.title': 'Where it is now',
  'now.title_named': 'Where {vehicle} is now',
  'now.update_failed': "Couldn't update just now. Trying again.",
  'now.live': 'Live location from {time}.',
  'now.replay': 'Recorded location from {time}.',
  'now.heading': 'Heading to {destination}',
  'now.next_stops': 'Next stops',
  'now.eta_live': 'Live',
  'now.eta_schedule': 'Timetable',
  'now.turns_around': 'It turns around at {stop}.',
  'now.continuing': 'Then it continues as route {route}.',
  'now.not_seen': "We can't see this {vehicle_noun} right now. Your plan works without it.",
  'now.checking': 'Checking where it is',
  'now.no_stops': 'No upcoming stops to show yet.',

  // ------------------------------------------------------------------------------------- the rider (CHANGES 2)
  'rider.distance': "You're about {distance} from it.",
  'rider.distance_short': '{distance} from you',
  'rider.show': 'Show me on the map',
  'rider.hint': 'Your location stays on this phone.',
  'rider.denied': 'Location is off. The map shows only the {vehicle_noun}.',
  'rider.waiting': 'Finding you',
  'rider.fit': 'Show both',
  'rider.you': 'You',

  // --------------------------------------------------------------------------------------------------- the map
  'map.your_stop': 'Your stop',
  'map.watch_again': 'Watch again',
  'map.credit': 'Map tiles:',
  'map.credit_ofm': 'OpenFreeMap',
  'map.credit_omt': '© OpenMapTiles',
  'map.credit_osm': '© OpenStreetMap contributors',
  'map.loading': 'Loading the map',
  'map.gesture': 'Use two fingers to move the map.',
  'map.mirror_stop': 'Your stop: {place}. The circle shows {radius} around it.',
  'map.mirror_circle': 'The circle shows {radius} around your stop.',
  'map.mirror_window': 'The replay shows {from} to {to}.',
  'map.mirror_vehicle': '{vehicle} passed {distance} from it at {time}.',
  'map.mirror_others': '{count} other {counted} moved through the replay area.',
  'map.mirror_now': 'Now: {state}',
  'map.mirror_next': 'Next stop: {stop} at {time}.',
  'map.mirror_you': "You: about {distance} from it.",
  'map.mirror_target': 'Meet it here: {place}.',
  'map.mirror_here': '{here}: the orange dot on the map.',
  // the plan's map: the one named vehicle's label, from the item tap
  'here.bag': 'Your bag is here',
  'here.phone': 'Your phone is here',
  'here.wallet': 'Your wallet is here',
  'here.keys': 'Your keys are here',
  'here.laptop': 'Your laptop is here',
  'here.other': 'Your item is here',
  // the line under the plan's map, from the latest /now (every 10 s)
  'planmap.rail': '{line} Line train[, now near {near}][, heading to {destination}].',
  'planmap.rail_plain': 'The train[, now near {near}][, heading to {destination}].',
  'planmap.bus': 'Route {route} bus[, now near {near}][, heading to {destination}].',
  'planmap.updated_s': 'Updated {seconds} s ago.',
  'planmap.updated_min': 'Updated {minutes} min ago.',
  'planmap.label': 'Where it is now',
  'map.no_replay': 'There is no replay for this one, so there is no map.',

  // ===== Plan screens (components/plan): the cards, the optional questions, the meet, the report and call. Not in PREP section 10: for team review.
  // the screen
  'plan.title': 'Your plan',
  'plan.lead': 'One step at a time. Start at the top.',
  'plan.loading': 'Putting your plan together',
  'plan.updating': 'Updating your plan',
  'plan.updated': 'Plan updated.',
  'plan.error_title': "Your plan didn't load",
  'plan.no_session': 'This visit has no match to plan from. Start again from the first question.',
  'plan.start_over': 'Start over',
  'plan.see_again': 'See the plan again',
  'plan.steps_label': 'Your plan, step by step',

  // card titles, by the API's card kind
  'plan.card.safety': 'Safety first',
  'plan.card.bag_staff': "Tell staff it's yours",
  'plan.card.meet': 'Meet it',
  'plan.card.too_far': 'No meet right now',
  'plan.card.tell_staff': 'Tell staff now',
  'plan.card.keep': 'The one you were on',
  'plan.card.report': 'Fill in the lost item form',
  'plan.card.call': 'Call Lost and Found',
  'plan.card.freeze_cards': 'Freeze your cards',
  'plan.card.phone_lost': 'Mark your phone as lost',
  'plan.card.police_property': 'MARTA Police property unit',
  'plan.card.meet_check': 'Can you meet it?',

  // a bus match, live: the meet needs to know where the rider is
  'near.offer': 'Close by? We can check if you can meet the bus.',
  'near.use_location': 'Use my location',
  'near.looking': 'Finding the stop nearest you',

  // "Think someone took it?": not a question any more (riders found it unnecessary), one quiet link at the end of the
  // plan; a tap sends taken "yes" (MARTA Police and 911 first, freeze cards), Undo sends "no"
  'taken.ask': 'Think someone took it?',
  'taken.added': 'Safety steps are now at the top of your plan.',
  'taken.undo': 'Undo',

  // where a card's facts come from (card.source); live and schedule use the chip's own words
  'source.static': 'Published info',
  'source.static_label': "From MARTA's published contacts, not live data",
  'plan.evidence': 'Seen {distance} from {place} at {time}.',
  'plan.evidence_label': 'Evidence',

  // the meet card (decision 9, CHANGES 2: the rider's walk from the live position)
  'meet.in_minutes': 'in about {minutes} min',
  'meet.you_walk': 'You: {minutes} min walk',
  'meet.you_there': "You're about there",
  'meet.edge': 'Wait back from the platform edge.',
  'meet.miss': "If you miss it, there's still a plan.",
  'meet.location_offer': 'Show my walk time',
  'meet.location_hint': 'Uses your location on this phone only. It is never sent or stored.',
  'meet.location_off': 'Location is off, so the walk time comes from your plan.',
  'meet.checking': 'Checked every 10 seconds while this page is open.',
  'meet.slow': 'I walk slowly or use a mobility aid',

  // "Where are you now?" (train matches only)
  'where.lead': 'So we can see if you can meet the train.',
  'where.station': 'At a station',
  'where.train': 'On another train',
  'where.elsewhere': 'Somewhere else',
  'where.change': 'Change',
  'where.answer_station': 'You are at {station}.',
  'where.answer_train': 'You are on the {line} train to {destination}.',
  'where.answer_elsewhere': "You're not at a station or on a train.",
  'where.no_location': "Without your location we can't time a meet. The rest of the plan still works.",
  'where.station_title': 'Which station are you at?',
  'where.station_search_label': 'Station name',
  'where.station_search': 'Search',
  'where.station_hint': 'Like "Five Points" or "West End"',
  'where.station_none': 'No station matches that. Try another name.',
  'where.location_line': 'Used once to find stations near you. Never stored.',
  'where.near_line': 'Used once to find the stop nearest you. Never stored.',
  'on_train.closest': 'Closest to you',
  'on_train.location_line': 'Used once to find trains near you. Never stored.',
  'answer.change': 'Change',

  // the report card
  'report.item_label': 'What does it look like? Optional',
  'report.item_hint': 'Like "a black backpack with a red tag". It stays on this phone and goes only in a message you send.',
  'report.copy_all': 'Copy all',
  'report.note_label': 'Note',
  'report.fields_label': 'Details for the form',
  'report.email_title': 'Or send it by email',
  'report.car_added': 'Rail Car Number added to the details.',
  'report.car_save': 'Add',
  'report.car_hint': 'The number painted inside the car, often near the end.',
  'item.phone': 'my phone',
  'item.wallet': 'my wallet',
  'item.bag': 'my bag',
  'item.keys': 'my keys',
  'item.laptop': 'my laptop or tablet',
  'item.other': 'an item',

  // the call card
  'call.script_title': 'What to say',
  'call.reminder_title': 'Call MARTA Lost and Found',
  'call.reminder_body': 'Call Lost and Found at {phone}. Have the details of your online report ready.',

  // the end
  'clear.confirm': 'Clear your answers from this phone and from our server now?',
  'clear.yes': 'Yes, clear now',
  'clear.cancel': 'Keep them',

  // the proof footer (decision 12)
  'proof.positions': '{count} vehicle positions recorded since {since}.',
  'proof.build': 'App build {web_sha}, data service {api_sha}',
  'proof.title': 'Where this comes from',

  // ===== Ways to get it back (components/plan/Reunion*): the reunion planner on the plan screen. For team review.
  // card titles for the API's reunion card kinds, and the copy keys api/README names for them (reunion_wait,
  // reunion_end, reunion_switch: a card rendered without the live section)
  'plan.card.wait_return': 'Wait for it to come back',
  'plan.card.meet_end': 'Meet it at the end of the line',
  'plan.card.switch_platform': 'Ride on and wait for it',
  reunion_wait: 'Wait for it at {stop}. It should come back around {time}[, between {window_from} and {window_to}].',
  reunion_end: 'Meet it at the end of the line at {stop}[, before it pulls out at {pulls_out}][, before it leaves at {departs}].',
  reunion_switch: 'Ride to {get_off_at}, step off and wait on the other platform. It should come back around {time}.',

  // the section
  'ways.best': 'Best way now',
  'ways.others': 'Other ways',
  'ways.others_count': 'Other ways ({count})',
  'ways.others_low': 'Might work, not certain',
  'ways.why_not': 'Why not other ways',
  'ways.none_title': "We can't time a way back right now.",
  'ways.none': 'Tell staff and send the report below: those are your best steps.',
  'ways.none_low': 'These might work, but they are not certain. Tell staff and send the report below as well.',
  'ways.expired': "That time has passed, so we're checking again.",
  'ways.updated_now': 'Updated just now',
  'ways.updated_s': 'Updated {seconds} s ago',
  'ways.updated_min': 'Updated {minutes} min ago',
  'ways.checking': 'Checked every 20 seconds while this page is open.',
  'ways.update_failed': "Couldn't update just now. We'll try again.",
  'ways.last_updated_now': 'Last updated just now.',
  'ways.last_updated_s': 'Last updated {seconds} s ago.',
  'ways.last_updated_min': 'Last updated {minutes} min ago.',
  'ways.changed': 'New best way to get it back.',
  'ways.gone': "We can't time a way back right now. The report is your best step.",
  'ways.map_target': 'Meet it here',
  'ways.list_label': 'Other ways, soonest first',

  // when (Times in New York; a window for every estimate)
  'ways.about': 'About {time}',
  'ways.between': 'between {from} and {to}',
  'ways.by_timetable': 'by the timetable',
  'ways.in_minutes': 'in about {minutes} min',
  'ways.when_live': 'At {time}.',
  'ways.when_estimate': 'About {time}, between {from} and {to}.',
  'ways.when_timetable': 'About {time}, by the timetable.',
  'ways.leave_by': 'Leave by {time}',
  'ways.leave_in': 'in about {minutes} min',
  'ways.leave_now': 'time to set off, at an easy pace',
  'ways.get_there': 'About {minutes} min to get there',
  'ways.platform_hint': 'Wait near the {platform_hint} of the platform, back from the edge.',
  'ways.car': 'You said it is in the {car} car.',

  // where the rider waits on a train platform
  'ways.platform_toward': 'the platform toward {toward}',
  'ways.platform_bound': 'the {bound} platform',
  'ways.platform_plain': 'the platform',
  'bound.N': 'northbound',
  'bound.S': 'southbound',
  'bound.E': 'eastbound',
  'bound.W': 'westbound',

  // basis and confidence (the chip says where the time comes from; colour only repeats it)
  'basis.live': 'Live',
  'basis.estimate': 'Estimate',
  'basis.timetable': 'Timetable',
  'basis.live_label': 'From its live position',
  'basis.estimate_label': 'Estimated from how it usually turns around',
  'basis.timetable_label': 'From the timetable only',
  'confidence.usual_bus': 'Usually comes back (about 9 in 10)',
  'confidence.low': 'Not certain',

  // one plain sentence per kind (never run, hurry or hold doors; a train platform change is never across tracks)
  'way.walk_ahead.bus': 'Walk to {stop} and meet it there.',
  'way.walk_ahead.rail': 'Go to {stop} and wait on {platform} for it.',
  'way.shared_track': 'Get off at {stop} and wait there on the same platform for it.',
  'way.switch_platform': 'Ride to {stop}, step off and wait on the other platform for it coming back.',
  'way.wait_here.bus': 'Wait at {stop} for it to come back.',
  'way.wait_here.rail': 'Stay at {stop} and wait on {platform} for it coming back.',
  'way.wait_on_return.bus': 'Walk to {stop} and wait there for it to come back.',
  'way.wait_on_return.rail': 'Go to {stop} and wait on {platform} for it coming back.',
  'way.meet_at_end.bus': 'Meet it at the end of the line at {stop}, before it pulls out.',
  'way.meet_at_end.rail': 'Ride to the end of the line at {stop} and meet it there before it leaves.',
  // the steps under the sentence
  'way.board': 'Take the {board_time} train from {board_at}[ toward {ride_toward}][, train run {ride_run}].',
  'way.ride_off': 'Step off at {get_off_at} around {off_time}.',
  'way.stay_on': 'Stay on your train[ toward {ride_toward}] to {get_off_at}, about {off_time}.',
  'way.wait_on': 'Then wait on {platform}.',
  'way.pulls_out': 'It pulls out around {pulls_out}.',
  'way.departs': 'It leaves around {departs}.',
  'way.vehicle': 'Look for {vehicle}[, vehicle {label}][, train run {train_run}].',

  // "When it pulls in": what to do as the bag's train or bus arrives, from the option's own facts (its line or
  // route, toward, time) and the rider's car tap. Checks, never promises: "check the seat", not "it will be there".
  'arrive.title': 'When it pulls in',
  'arrive.train': 'Your train: {line}[ toward {toward}], due about {time}.',
  'arrive.train_plain': 'Your train is due about {time}[, toward {toward}].',
  'arrive.train_board': 'Let people off first, then get on[ the {car} car, where you were].',
  'arrive.train_check': "Check the seat and the floor. If it isn't there, press the car's intercom to tell the operator, or tell station staff.",
  'arrive.train_step_off': 'You can step off at the next station and come back.',
  'arrive.train_safe': 'Never hold the doors.',
  'arrive.bus': 'Your bus: Route {route}[ toward {toward}], due about {time}.',
  'arrive.bus_plain': 'Your bus is due about {time}[, toward {toward}].',
  'arrive.bus_board': 'Get on at the front and tell the driver you left something on this bus earlier. Check where you sat.',
  'arrive.bus_safe': 'Never chase a bus or step in front of it.',
  // the one-line version on the other ways
  'arrive.short_train': 'When it pulls in: {line}[ toward {toward}]. Let people off first, then check the seat and the floor.',
  'arrive.short_train_plain': 'When it pulls in, let people off first, then check the seat and the floor.',
  'arrive.short_bus': 'When it pulls in: Route {route}. Get on at the front and tell the driver you left something on it.',
  'arrive.short_bus_plain': 'When it pulls in, get on at the front and tell the driver you left something on it.',

  // "Your best plan": the recommended way as numbered steps, from the option's own facts (reunionModel wayStepLines).
  // {item_pos} is the item tap's possessive ("bag's", "keys'", else "item's"). A step whose facts are missing is left out.
  'plan.card.best': 'Your best plan',
  'backup.toggle': "Didn't find it?",
  'backup.label': 'If it was not there',
  'steps.label': 'Your best plan, step by step',
  'steps.stay_at': 'Stay at {stop}.',
  'steps.walk_to': 'Walk to {stop}[ (about {walk_min} min)].[ Leave by {leave_by}.]',
  'steps.wait_on': 'Wait on {platform}.',
  'steps.wait_bus': 'Wait at the stop.',
  'steps.turns_back_train': "Your {item_pos} train turns around at the end of the line and comes back.",
  'steps.turns_back_bus': "Your {item_pos} bus turns around at the end of its route and comes back.",
  'steps.should_arrive': 'It should arrive about {time}[ (between {window_from} and {window_to})].',
  'steps.take_train': 'Take the {board_time} train[ toward {ride_toward}] from {board_at}.[ Leave by {leave_by}.]',
  'steps.stay_on': 'Stay on your train.',
  'steps.get_off': 'Get off at {stop}[ at about {off_time}].',
  'steps.cross': 'Cross to the platform toward {toward}.',
  'steps.cross_plain': 'Cross to the platform for trains going the other way.',
  'steps.same_platform': 'Wait on the same platform.',
  'steps.train_arrives': "Your {item_pos} train arrives about {time}[ (between {window_from} and {window_to})].",
  'steps.bus_arrives': "Your {item_pos} bus arrives about {time}[ (between {window_from} and {window_to})].",
  'steps.walk_to_end': 'Walk to {stop}, the end of the line[ (about {walk_min} min)].[ Leave by {leave_by}.]',
  'steps.ride_to_end': 'Take the {board_time} train from {board_at} to {stop}, the end of the line.[ Leave by {leave_by}.]',
  'steps.bus_waits': 'The bus waits there until about {pulls_out}.',
  'steps.train_waits': 'The train waits there until about {departs}.',
  'steps.staff': "Tell any staff you pass that it's yours.",
  'item_pos.bag': "bag's",
  'item_pos.phone': "phone's",
  'item_pos.wallet': "wallet's",
  'item_pos.keys': "keys'",
  'item_pos.laptop': "laptop's",
  'item_pos.other': "item's",

  // short names, for "Why not other ways"
  'way.name.walk_ahead': 'Walk ahead and meet it',
  'way.name.shared_track': 'Get off where both trains stop',
  'way.name.switch_platform': 'Ride on and change platforms',
  'way.name.wait_here': 'Wait where you are',
  'way.name.wait_on_return': 'Wait at a stop or station nearby',
  'way.name.meet_at_end': 'Meet it at the end of the line',
  'why.names_two': '{a} or {b}',
  'why.names_many': '{list}, or {last}',

  // every reason id the API can give (models.ReunionReason)
  'why.ahead': 'It is already past the places you could reach in time.',
  'why.too_far': 'It is too far to reach in time.',
  'why.block_ends': 'This bus goes out of service after this trip, so it is not coming back soon. The report is your best step.',
  'why.interline_away': 'After this trip it goes on to another route, so it will not pass here again for hours.',
  'why.may_leave_service': 'It may not come back tonight. The timetable has no more trips back, so the report is the safe step.',
  'why.night_short_turn': 'Tonight its trip back ends before your station, so it may not come back here. The report is the safe step.',
  'why.not_live': "We can't see it live right now, so we can't time this.",
  'why.no_location': "We don't know where you are yet, so we can't time this.",
  'why.layover_too_short': "It doesn't wait at the end of the line long enough to get there in time.",
  'why.not_shared': "Your train and its train don't stop at the same platform.",
  'why.not_dead_end': "Trains don't wait at a platform at that end of the line.",
  'why.not_turned': "It hasn't turned around at the end of the line yet. We keep checking.",
  'why.not_on_train': 'This is for a rider on another train.',
  'why.on_train': "You're on a train, so this doesn't apply.",
  'why.no_trip': "We can't tell which trip it is on, so we can't time its way back.",
  'why.tie': "Two still fit, and this doesn't work for both.",
  'why.too_soon': 'It comes back before you could get there.',
  'why.no_return_stop': "Its trip back doesn't stop near you.",
  'why.not_seen': "It hasn't been seen since the end of the line, so it may be out of service. The report is the safe step.",
  'why.already_left': 'It has already left the end of the line.',
  'why.no_train_to_end': 'No train gets you to the end of the line in time.',
  'why.not_matched': "We couldn't name the exact one, so we can't time a way back. The report is your best step.",

  // every note id the API can give (models.ReunionNote)
  'note.usually_comes_back': 'It usually comes back this way, about 9 times in 10.',
  'note.running_very_late': 'It is very late right now, so this time is less certain.',
  'note.evening_not_certain': 'After 7 PM some trains stop for the night, so it may not come back tonight. The report is the safe step.',
  'note.after_midnight_unmeasured': 'This late at night it may not come back at all. The report is the safe step.',
  'note.vanished_still_expected': 'It dropped off the live feed at the end of the line, which is normal there. It is still expected.',
  'note.watch_train': 'It is on its way back now: the train due around {time}.',
  'note.watch_after_turn': "Other trains may stop first. We'll say which one is yours once it turns around.",
  'note.schedule_not_in_effect': "Its trip back isn't on today's timetable, so this time is less certain.",
  'note.change_platform': 'Change to the platform for trains going the other way.',
  'note.assumed_location': 'We guessed you are still where you got off. Tell us where you are for a better plan.',
  'note.assumed_location_plain': 'We guessed you are still where you got off.',
  'note.may_come_before_you': 'It may come back a little sooner, so be there by {window_from}.',
  'note.night': "If you're alone, ask station staff to help.",
  'note.timetable_only': 'This time is from the timetable, not a live position.',
  'note.at_the_end_of_the_line': 'It waits there a few minutes before it heads back.',
  'note.late_still_expected': 'It is a little late, but still expected. Stay where you are.',

  // ===== Laptop layout (components/screens/ClueAside): the right pane beside the questions. For team review.
  'aside.label': 'Your clue so far',
  'aside.title': 'Your clue so far',
  'aside.lead': 'Each tap narrows it down. Your answers delete themselves 2 hours after you stop.',
  'aside.what': 'What',
  'aside.ride': 'Ride',
  'aside.stop': 'Got off at',
  'aside.when': 'When',
  'aside.waiting': 'Not answered yet',
  'aside.end': 'Then we find the exact one and where it is now.',
  'aside.not_marta': 'Not on MARTA',
  'aside.end_elsewhere': 'Then a short plan for each place you name.',
  'aside.map_label': 'Map and live view',
  // the installed app with no connection: the recorded ride is one tap away (home)
  'home.offline': 'No connection right now.',
  'home.offline_hint': 'You can still see how it works on a recorded ride.',
  'home.recorded': 'Recorded example',
  'home.expired': 'Your earlier visit has ended, and its answers are deleted.',
  'home.expired_hint': 'Start again whenever you are ready.',
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

/** t() for an id computed at runtime (`plan.card.${kind}`), where the compiler can't see which slots it takes. */
export function tLoose(id: CopyId, slots: Readonly<Record<string, SlotValue | null | undefined>> = {}): string {
  return render(COPY[id], slots, id)
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
