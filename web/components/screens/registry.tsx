import type { ComponentType } from 'react'
import type { Step } from '@/lib/flow'
import type { FlowSession } from '@/lib/useFlow'
import { HomeScreen } from './HomeScreen'

// One screen component per flow step. Screen builders add their step here; AppShell renders the registered screen
// for the current step, or PlaceholderScreen until one exists. Heavy screens (the map) should come in through
// next/dynamic so MapLibre loads only on the results screen (decision 12).
export interface ScreenComponentProps {
  flow: FlowSession
}

export const SCREENS: Partial<Record<Step, ComponentType<ScreenComponentProps>>> = {
  home: HomeScreen,
}
