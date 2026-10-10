import type { ComponentType } from 'react'
import type { Step } from '@/lib/flow'
import type { FlowSession } from '@/lib/useFlow'
import { INTAKE_SCREENS } from '@/components/intake/IntakeFlow'
import { PlanScreen } from '@/components/plan/PlanScreen'
import { ResultsScreen } from '@/components/results/ResultsScreen'
import { HomeScreen } from './HomeScreen'

// One screen component per flow step (lib/flow.ts):
//   home -> what -> mode -> off -> when -> done -> results -> plan     (tell: "Type it instead", a detour)
//                    \-> not_marta -> elsewhere_what -> elsewhere_plan ("Not on MARTA")
// AppShell renders the registered screen for the current step. MapLibre is not imported here: MatchMap loads it with
// a dynamic import, so the home screen and the questions never download the map library (decision 12).
export interface ScreenComponentProps {
  flow: FlowSession
}

/** The plan reads the rider's pick from results (flow.pick); after a reload the plan's own stored tap stands in. */
function PlanStep({ flow }: ScreenComponentProps) {
  return <PlanScreen flow={flow} vehicleKeys={flow.pick} />
}

export const SCREENS: Partial<Record<Step, ComponentType<ScreenComponentProps>>> = {
  home: HomeScreen,
  ...INTAKE_SCREENS,
  results: ResultsScreen,
  plan: PlanStep,
}
