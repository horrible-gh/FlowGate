import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

// flowgate.default.0412 T0004 / NR0003: 다이얼로그 바깥(오버레이 자신) 클릭으로 닫히거나
// 취소되던 19개 지점은 개별 `@click.self` 바인딩이 원인이었다. 이 계약 테스트는 그
// 19개 지점이 속한 18개 파일에서 `@click.self`가 다시 추가되지 않는지만 지킨다 —
// 팝오버/컨텍스트 메뉴/모바일 사이드바처럼 다이얼로그가 아닌 나머지 UI는 이 작업의
// 범위가 아니므로(T0004 §3.5) 여기서 검사하지 않는다.
const CLIENT_DIR = resolve(__dirname, '../..')
const COMPONENTS_DIR = resolve(CLIENT_DIR, 'src/main/components')

const TARGET_FILES = [
  'AiInvokeDialog.vue',
  'ClipboardFallbackModal.vue',
  'ContinuousWarningDialog.vue',
  'ContinuousWorkDialog.vue',
  'GitActionMenu.vue',
  'GitBaseDirtyDialog.vue',
  'GitConflictResolverDialog.vue',
  'GitUntrackedConflictDialog.vue',
  'GroupDiscardModal.vue',
  'GroupInfoModal.vue',
  'GroupTokenIssueModal.vue',
  'MainPanel.vue',
  'MentionMessageDialog.vue',
  'NextActionModal.vue',
  'QaHistoryDialog.vue',
  'ReviewHistoryDialog.vue',
  'WorkPlanEditor.vue',
  'WorkPlanProposalDialog.vue',
]

describe('dialog overlays never rebind @click.self (0412 T0004/NR0003)', () => {
  it.each(TARGET_FILES)('%s has no @click.self binding', (file) => {
    const source = readFileSync(resolve(COMPONENTS_DIR, file), 'utf8')
    expect(source).not.toContain('@click.self')
  })

  // The three backdrop relay functions were dead code once @click.self was removed
  // and were deleted (T0004 §3.2) — a regression that re-adds @click.self must not
  // silently resurrect them as unused code either.
  it.each([
    'AiInvokeDialog.vue',
    'GitBaseDirtyDialog.vue',
    'GitUntrackedConflictDialog.vue',
  ])('%s no longer defines onBackdrop', (file) => {
    const source = readFileSync(resolve(COMPONENTS_DIR, file), 'utf8')
    expect(source).not.toContain('onBackdrop')
  })
})
