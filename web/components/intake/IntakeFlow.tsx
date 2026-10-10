'use client'

import type { ComponentType } from 'react'
import type { Step } from '@/lib/flow'
import type { FlowSession } from '@/lib/useFlow'
import { DoneScreen } from './DoneScreen'
import { ElsewherePlanScreen } from './ElsewherePlanScreen'
import { ElsewhereScreen } from './ElsewhereScreen'
import { ModeScreen } from './ModeScreen'
import { OffScreen } from './OffScreen'
import { TellScreen } from './TellScreen'
import { WhatScreen } from './WhatScreen'
import { WhenScreen } from './WhenScreen'

// The intake, driven by lib/flow.ts: four questions (what, bus or train, where you got off, about when), TellMe's
// "Type it instead" detour, the done screen that POSTs the match, and the "Not on MARTA" path with its plan.
//
// Mounting (components/screens/registry.tsx):
//   import { INTAKE_SCREENS } from '@/components/intake/IntakeFlow'
//   export const SCREENS = { home: HomeScreen, ...INTAKE_SCREENS, results: ..., plan: ... }
// or render <IntakeFlow flow={flow} /> for any step in INTAKE_STEPS.

export const INTAKE_STEPS = ['what', 'mode', 'off', 'when', 'tell', 'done', 'not_marta', 'elsewhere_plan'] as const satisfies readonly Step[]
export type IntakeStep = (typeof INTAKE_STEPS)[number]

const SCREEN: Record<IntakeStep, ComponentType<{ flow: FlowSession }>> = {
  what: WhatScreen,
  mode: ModeScreen,
  off: OffScreen,
  when: WhenScreen,
  tell: TellScreen,
  done: DoneScreen,
  not_marta: ElsewhereScreen,
  elsewhere_plan: ElsewherePlanScreen,
}

export const isIntakeStep = (s: Step): s is IntakeStep => (INTAKE_STEPS as readonly string[]).includes(s)

/** The screen for the current step, or null when the step isn't part of the intake (home, results, plan). */
export function IntakeFlow({ flow }: { flow: FlowSession }) {
  const step = flow.state.step
  if (!isIntakeStep(step)) return null
  const S = SCREEN[step]
  return <S flow={flow} />
}

/** For the registry: every intake step renders IntakeFlow. */
export const INTAKE_SCREENS: Partial<Record<Step, ComponentType<{ flow: FlowSession }>>> = Object.fromEntries(
  INTAKE_STEPS.map((s) => [s, IntakeFlow]),
)
