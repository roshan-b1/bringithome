import { notFound } from 'next/navigation'
import { PlanHarness } from './PlanHarness'

// Dev-only harness for the plan screens (components/plan): real sessions from the live API through the dev proxy,
// replayed at a recorded moment, or the mock fixture set. The static export renders the 404 page here.
export default function Page() {
  if (process.env.NODE_ENV === 'production') notFound()
  return <PlanHarness />
}
