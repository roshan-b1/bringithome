import { notFound } from 'next/navigation'
import { Harness } from './Harness'

// Dev-only harness for the intake screens (components/intake), against the live API through the dev proxy or a
// fixture set. The static export renders the 404 page here, so nothing of it ships to riders. See Harness.tsx for
// the URL parameters.
export default function Page() {
  if (process.env.NODE_ENV === 'production') notFound()
  return <Harness />
}
