// v02test.0096 F96-3: the AI 관리 tabs. The taskbar window shows 내 AI / 기타 설정 / 방침; the full screen adds its AI
// evaluation and AI group tabs between the list and 기타 설정 (AI 목록 · AI 평가 · AI 그룹 · 기타 설정 · 방침).
export type AiWindowTab = 'ai' | 'other' | 'policy'
export type AiManageTab = AiWindowTab | 'evaluation' | 'groups'

export const AI_WINDOW_TABS: readonly AiWindowTab[] = ['ai', 'other', 'policy']

/** A `?tab=` value as a tab. Links from before 방침 (`question-policy`) open 방침; anything else opens the list. */
export function parseAiManageTab(value: unknown): AiManageTab {
  if (value === 'evaluation' || value === 'groups' || value === 'other' || value === 'policy') return value
  if (value === 'question-policy') return 'policy'
  return 'ai'
}

export function isAiWindowTab(value: unknown): value is AiWindowTab {
  return typeof value === 'string' && (AI_WINDOW_TABS as readonly string[]).includes(value)
}
