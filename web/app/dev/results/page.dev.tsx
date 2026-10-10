import { notFound } from 'next/navigation'
import { Harness } from './Harness'

// Dev-only harness for the results screens (real sessions from the live API through the dev proxy, and the mock
// fixture set). The static export renders the 404 page here, so nothing of it ships to riders.
export default function Page() {
  if (process.env.NODE_ENV === 'production') notFound()
  return <Harness />
}
