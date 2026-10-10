import { useMemo, type ComponentType } from 'react'
import type { Step } from '@/lib/flow'
import { pickedSession } from '@/lib/riderPick'
import type { FlowSession } from '@/lib/useFlow'
import { INTAKE_SCREENS } from '@/components/intake/IntakeFlow'
import { PlanScreen } from '@/components/plan/PlanScreen'
import { ResultsScreen } from '@/components/results/ResultsScreen'
import { HomeScreen } from './HomeScreen'

// One screen component per flow step (lib/flow.ts):
//   home -> ride -> off -> [mode] -> when -> done -> results -> plan   (tell: "Say it in one sentence", a detour)
//            \-> not_marta -> elsewhere_what -> elsewhere_plan ("Not on MARTA")
// AppShell renders the registered screen for the current step. MapLibre is not imported here: MatchMap loads it with
// a dynamic import, so the home screen and the questions never download the map library (decision 12).
export interface ScreenComponentProps {
  flow: FlowSession
}

/**
 * The plan reads the rider's pick from results (flow.pick); after a reload the plan's own stored tap stands in. A
 * vehicle the rider tapped themselves (flow.riderPick, a closest-fit card at uncertain) is planned as the match:
 * the plan sees the session matched to it (riderPick.pickedSession, the API's as_picked) and its requests carry
 * picked, so the meet, the reunion and the map all work for it.
 */
function PlanStep({ flow }: ScreenComponentProps) {
  const own = flow.riderPick ?? null
  const result = flow.session
  const session = useMemo(() => (result && own ? { ...result, data: pickedSession(result.data, own) } : result), [result, own])
  const view = session === result ? flow : { ...flow, session }
  return <PlanScreen flow={view} vehicleKeys={flow.pick ?? own} />
}

export const SCREENS: Partial<Record<Step, ComponentType<ScreenComponentProps>>> = {
  home: HomeScreen,
  ...INTAKE_SCREENS,
  results: ResultsScreen,
  plan: PlanStep,
}
