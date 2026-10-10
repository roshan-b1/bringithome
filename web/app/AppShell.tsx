'use client'

import { PlaceholderScreen } from '@/components/screens/PlaceholderScreen'
import { SCREENS } from '@/components/screens/registry'
import { useFlowSession } from '@/lib/useFlow'

// The one page's client root (decision 12: one page, a client state machine). It owns the flow session and
// renders the screen registered for the current step. The first render is always the home screen, which is what
// the static export prerendered, so hydration matches; a saved visit swaps in right after the resume.
export function AppShell() {
  const flow = useFlowSession()
  const StepScreen = SCREENS[flow.state.step] ?? PlaceholderScreen
  return <StepScreen flow={flow} />
}
