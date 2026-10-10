// flowgate.v02.0051 stage 2 — the 확인 card's settings tab (0035 v7 confirmSettings): 용도 (중간 승인 /
// 그룹 종료 — under discussion, v7 §5, shown as display only with the 토론 중 tag) and the Git 반영 card
// (반영 방식 병합 / 커밋만 / 보류 · 병합 대상 · 원격 반영 · 브랜치 보관). In 0048 the Git choice is asked
// in the final-approval dialog (check feature, T#12) and no setting is stored on the Node, so the view
// knows nothing and nothing can be changed here (0051-R §2.3: shown, inactive). Pure functions.
import type { InspectorTarget } from '../../inspectorTarget'

export type CheckUsage = 'mid' | 'end'
export type CheckScope = 'merge' | 'commit' | 'hold'

export interface CheckSettingsView {
  /** The card's usage, or null when not known (it is under discussion and not stored). */
  readonly usage: CheckUsage | null
  readonly scope: CheckScope | null
  /** Merge target branch, or null. */
  readonly target: string | null
  readonly push: boolean | null
  readonly keepBranch: boolean | null
  /** The settings can be changed (a server API exists). */
  readonly connected: boolean
}

export type CheckSettingsFacts = Pick<InspectorTarget, 'docId' | 'readOnly'>

export const CHECK_SCOPES: readonly CheckScope[] = ['merge', 'commit', 'hold']

/** The real view: nothing stored on the Node, nothing changeable here. */
export function checkSettingsView(_facts: CheckSettingsFacts): CheckSettingsView {
  return { usage: null, scope: null, target: null, push: null, keepBranch: null, connected: false }
}
