import { notFound } from 'next/navigation'
import { Gallery } from './Gallery'

// Dev-only gallery of the base components, for screen builders and screenshot review. The static export renders
// the 404 page here, so nothing of it ships to riders.
export default function Page() {
  if (process.env.NODE_ENV === 'production') notFound()
  return <Gallery />
}
