// flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's settings tab (0035 v7 reqSettings): AI 위임
// 끄기 / 켜기, and while ON its 범위 (끝까지 / 계획 확인 후), AI Team and the 안전 한도 (최대 생성 Node ·
// 총 검수 · 재작업 · 카드별 재시도 · 재계획). AI delegation has no server API in 0051 (R §3.3), so the
// real view is always OFF and not changeable — the v7 OFF explanation is shown and the switch is drawn
// disabled with its reason. The ON shape exists for the fixture preview only. Pure functions.
import type { InspectorTarget } from '../../inspectorTarget'

export type RequirementDelegation = 'off' | 'on'
export type RequirementScope = 'end' | 'confirm'

export const REQUIREMENT_LIMIT_KEYS = ['max_nodes', 'total_reviews', 'retries_per_card', 'replans'] as const

export type RequirementLimitKey = (typeof REQUIREMENT_LIMIT_KEYS)[number]

export interface RequirementLimit {
  readonly key: RequirementLimitKey
  /** The value, or null when nothing is known. */
  readonly value: number | null
  /** The limit the group reached (v7 req.d.limit: shown red). */
  readonly hot: boolean
}

export interface RequirementSettingsView {
  readonly delegation: RequirementDelegation
  readonly scope: RequirementScope
  /** AI Team id, or null. */
  readonly team: string | null
  readonly teamOptions: readonly { readonly value: string; readonly label: string }[]
  readonly limits: readonly RequirementLimit[]
  /** Delegation is running: the settings are locked (v7 🔒 위임 진행 중). */
  readonly locked: boolean
  /** The settings can be changed (a server API exists). */
  readonly connected: boolean
}

export type RequirementSettingsFacts = Pick<InspectorTarget, 'docId' | 'readOnly'>

/** The real view: OFF, nothing known, nothing changeable. */
export function requirementSettingsView(_facts: RequirementSettingsFacts): RequirementSettingsView {
  return {
    delegation: 'off',
    scope: 'end',
    team: null,
    teamOptions: [],
    limits: REQUIREMENT_LIMIT_KEYS.map((key) => ({ key, value: null, hot: false })),
    locked: false,
    connected: false,
  }
}
