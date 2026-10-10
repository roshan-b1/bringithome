'use client'

import { ClueAside } from '@/components/screens/ClueAside'
import { ScreenBoundary } from '@/components/screens/CrashScreen'
import { PlaceholderScreen } from '@/components/screens/PlaceholderScreen'
import { DefaultAsideProvider } from '@/components/ui/Aside'
import { SCREENS } from '@/components/screens/registry'
import { useFlowSession } from '@/lib/useFlow'

// The one page's client root (decision 12: one page, a client state machine). It owns the flow session and
// renders the screen registered for the current step. The first render is always the home screen, which is what
// the static export prerendered, so hydration matches; a saved visit swaps in right after the resume.
export function AppShell() {
  const flow = useFlowSession()
  const StepScreen = SCREENS[flow.state.step] ?? PlaceholderScreen
  // the laptop's right pane, for any screen that has no map of its own: the clue so far (never drawn on a phone)
  return (
    <DefaultAsideProvider value={<ClueAside answers={flow.state.answers} />}>
      {/* a screen that throws keeps the frame, the rider's report and the safety footer (CrashScreen) */}
      <ScreenBoundary
        answers={flow.state.answers}
        resetKey={flow.state.step}
        onRestart={flow.restart}
        onRecorded={() => flow.goTo('done')}
      >
        <StepScreen flow={flow} />
      </ScreenBoundary>
    </DefaultAsideProvider>
  )
}
