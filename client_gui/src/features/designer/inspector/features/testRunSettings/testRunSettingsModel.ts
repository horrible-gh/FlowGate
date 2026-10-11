// flowgate.v02.0051 stage 2 — the 테스트 card's own settings (0035 v7 testSettings): the 실행 block — v7 took
// the card-wide automated / manual choice out: the execution mode is per Case, the settings tab shows the
// counts only, and the manual · external result flow is not drawn. Pure functions.
// flowgate.v02.0053 T#7 — the block stands after the settings feature's 지시서 · 검수 뒤 진행 · 시나리오 작성
// (v7 order; those are the card's T#2 settings). The card stores nothing for the 실행 block (each Case keeps
// its mode in the TS, 0549 contract 2), so the block shows the TS's Case counts by mode, read from the card's
// TS (its 지시서 line, else the bound TS); "—" while nothing is known.
import type { InspectorTarget } from '../../inspectorTarget'

export interface TestRunModes {
  readonly automated: number | null
  readonly manual: number | null
  readonly external: number | null
}

export interface TestRunSettingsView {
  readonly modes: TestRunModes
}

/** The parts of the test view the counts come from (the `@/features/document` TestSpecView). */
export interface TestRunSpecFacts {
  readonly contractVersion: number | null
  readonly cases: readonly { readonly executionMode: string | null }[]
}

export type TestRunSettingsFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'cardDocuments'>

/** The TS whose Cases the block counts: the card's 지시서 line, else the bound TS; null without one. */
export function testRunSettingsSpecDocId(facts: TestRunSettingsFacts): string | null {
  const own = facts.cardDocuments.find((line) => line.slot === 'instruction')?.docId ?? null
  if (own) return own
  return facts.docId !== null && facts.docType?.trim().toUpperCase() === 'TS' ? facts.docId : null
}

const NOTHING: TestRunModes = { automated: null, manual: null, external: null }

/** The Case counts by mode of a specification TS (contract 2); nothing known otherwise. */
export function testRunSettingsView(spec: TestRunSpecFacts | null): TestRunSettingsView {
  if (!spec || spec.contractVersion !== 2) return { modes: NOTHING }
  const count = (mode: string) => spec.cases.filter((item) => item.executionMode === mode).length
  return { modes: { automated: count('automated'), manual: count('manual'), external: count('external') } }
}
