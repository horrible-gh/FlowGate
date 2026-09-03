// (A) 유지 — 0394 T0016 / NR0003 §6.3.
// 잠긴 목록(UI)은 T0004(NR0003 후속)에서 edit 모달에서 제거되었다.
// locked/pending 안내 UI와 관련 CSS가 더 이상 렌더링·선언되지 않음을 정적 source assert로 검증한다.
// 같은 컴포넌트의 동작 단언은 WorkflowDecisionModal.config.spec.ts에서 마운트로 수행한다.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  join(process.cwd(), 'src/main/components/WorkflowDecisionModal.vue'),
  'utf8',
)

const lockedClasses = [
  '.wem-locked-section',
  '.wem-locked-title',
  '.wem-locked-empty',
  '.wem-locked-list',
  '.wem-locked-item',
  '.wem-locked-label',
  '.wem-status-badge',
]

describe('WorkflowDecisionModal locked/pending UI removal (T0004)', () => {
  it('renders no locked section UI', () => {
    expect(source).not.toMatch(/wem-locked-section/)
    expect(source).not.toMatch(/workflow_edit_modal\.locked_/)
    expect(source).not.toMatch(/workflow_edit_modal\.pending_section_title/)
  })

  it('leaves no dead locked/pending CSS rules', () => {
    for (const cls of lockedClasses) {
      expect(source.includes(cls)).toBe(false)
    }
  })

  it('keeps the all-done continuation notice', () => {
    expect(source).toMatch(/wem-all-done/)
    expect(source).toMatch(/workflow_edit_modal\.all_done/)
  })

  it('keeps lockedItems sequence-numbering semantics', () => {
    expect(source).toMatch(/mode === 'edit' \? lockedItems\.length : 0\) \+ idx \+ 1/)
  })
})
