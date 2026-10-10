'use client'

import { BigButton, type BigButtonVariant } from '@/components/ui/BigButton'
import type { IconName } from '@/components/ui/Icon'
import type { CardAction } from '@/contracts/api'
import { tFacts, type CopyId } from '@/lib/copy'
import { displayForHref } from '@/lib/contacts'
import { CopyButton } from './CopyButton'
import { NoBreak } from './FactText'
import { directionsFromValue } from './reunionModel'
import { useAppleMaps } from './useAppleMaps'

// A plan card's action, as the API sent it: {type, label_id, value}. The words come from copy.ts by label_id, the
// {number} in them from the bundled contacts by the action's own href, so a label never shows a number the link
// doesn't dial. An action whose label or number can't be filled is left out rather than shown half-made.

const LABEL: Record<string, CopyId> = {
  call_police: 'action.call_police',
  call_911: 'action.call_911',
  keep_copy: 'action.keep_copy',
  report_open_form: 'action.report_open_form',
  report_copy: 'action.report_copy',
  call_lost_found: 'action.call_lost_found',
  email_lost_found: 'action.email_lost_found',
  call_reminder: 'action.call_reminder',
  customer_service: 'action.customer_service',
  route_schedule_info: 'action.route_schedule_info',
  call_police_property: 'action.call_police_property',
}

const ICON: Record<CardAction['type'], IconName> = { tel: 'phone', mailto: 'mail', link: 'link', copy: 'copy', ics: 'calendar', directions: 'pin' }

// a directions action's value is the stop's own public "lat,lon" (never the rider's), opened in the phone's own
// maps app: Apple Maps on iPhone, iPad and Mac Safari, Google Maps elsewhere (reunionModel.directionsUrl)

/** The action's words ("Call MARTA Police 404-848-4900"), or null when they can't be filled honestly. */
export function actionLabel(a: CardAction): string | null {
  // a label_id this build doesn't know still gets the type's own plain words for a directions link
  const id = LABEL[a.label_id] ?? (a.type === 'directions' ? 'action.directions' : undefined)
  if (!id) return null
  const number = a.type === 'tel' ? displayForHref(a.value) : null
  return tFacts(id, number ? { number } : {})
}

export function ActionButton({
  action,
  variant = 'secondary',
  href,
  download,
}: {
  action: CardAction
  variant?: BigButtonVariant
  /** overrides the value as the link (the composed mailto, the built calendar file) */
  href?: string | null
  /** a file name for a data: download (the reminder) */
  download?: string
}) {
  const apple = useAppleMaps()
  const label = actionLabel(action)
  if (!label) return null
  if (action.type === 'copy') return <CopyButton text={action.value} label={label} variant={variant === 'primary' ? 'primary' : 'secondary'} />
  const target = href ?? (action.type === 'directions' ? directionsFromValue(action.value, apple) : action.value)
  if (!target) return null
  const external = action.type === 'link' || action.type === 'directions'
  return (
    <BigButton
      variant={action.label_id === 'call_911' ? 'danger' : variant}
      size="md"
      icon={ICON[action.type]}
      iconEnd={external ? 'next' : undefined}
      href={target}
      download={download}
      target={external ? '_blank' : undefined}
      rel={external ? 'noopener noreferrer' : undefined}
    >
      {/* the number never breaks at its hyphens ("404-" over "848-4900" reads as two numbers) */}
      <NoBreak text={label} />
    </BigButton>
  )
}
