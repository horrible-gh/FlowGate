// flowgate.v02.0051 stage 2 — the 테스트 card's own settings (0035 v7 testSettings): 지시 (TS) 사용 /
// 생략, 검수 뒤 진행, and the 실행 block — v7 took the card-wide automated / manual choice out: the
// execution mode is per Case (TSC), the settings tab shows the counts only, and the manual · external
// result flow is not drawn. The 케이스 작성 AI / 검수 fields are the settings feature's run settings
// (the TS step's provider · reviewer · review count). None of these three items is stored on a Node yet
// and the Case counts are not read here (the testSpec feature reads the test view), so the view knows
// nothing and nothing can be changed (0051-R §2.3). Pure functions.
import type { InspectorTarget } from '../../inspectorTarget'

export type TestInstruction = 'use' | 'omit'
export type TestPolicy = 'follow' | 'human'

export interface TestRunModes {
  readonly automated: number | null
  readonly manual: number | null
  readonly external: number | null
}

export interface TestRunSettingsView {
  readonly instruction: TestInstruction | null
  readonly policy: TestPolicy | null
  readonly modes: TestRunModes
  /** A run is live: the settings are locked (v7 🔒 실행 중). */
  readonly locked: boolean
  /** The settings can be changed (a server API exists). */
  readonly connected: boolean
}

export type TestRunSettingsFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'nodeRun'>

/** Node run statuses during which the card's settings are locked (v7 `set: 'lock'`). */
export const TEST_RUN_LIVE_STATUSES: readonly string[] = ['running', 'queued', 'executing', 'preparing', 'cancelling']

/** The real view: nothing stored, counts not read here, locked while the Graph node run is live. */
export function testRunSettingsView(facts: TestRunSettingsFacts): TestRunSettingsView {
  const status = facts.nodeRun?.status?.trim().toLowerCase() ?? ''
  return {
    instruction: null,
    policy: null,
    modes: { automated: null, manual: null, external: null },
    locked: TEST_RUN_LIVE_STATUSES.includes(status),
    connected: false,
  }
}
