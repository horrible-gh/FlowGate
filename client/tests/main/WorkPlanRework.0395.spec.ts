import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), 'utf8')
}

describe('0395 작업계획 재작업 회귀 가드', () => {
  it('워크플로 제목줄 단추는 없고 WP 칸 진입점은 남긴다', () => {
    const text = source('src/main/components/DocWorkflow.vue')
    expect(text).not.toContain('wf-wp-btn')
    expect(text).toContain("if (s.code === WORK_PLAN_TYPE)")
    expect(text).toContain('emitCreateWorkPlan()')
  })

  it('수량/단계 화면은 시안의 단일 DOM과 스크롤 구조만 쓴다', () => {
    const text = source('src/main/components/WorkPlanEditor.vue')
    for (const removed of [
      'wp-qty-add',
      'wp-qty-zero-hint',
      'removeConfirmVisible',
      'providerFilter',
      'wp-step-table',
      'wp-step-cards',
      'wp-ai-badge',
      'wp-unassigned-badge',
      'wp-unavailable-badge',
    ]) expect(text).not.toContain(removed)
    expect(text).toContain('wp-step-head')
    expect(text).toContain('wp-step-list')
    expect(text).toContain('342px')
    expect(text).toContain('renderedCountedTypes')
  })

  it('생성 대화상자는 전체 셀 수 있는 타입을 정본에 보낸다', () => {
    const text = source('src/main/components/WorkPlanCreateDialog.vue')
    expect(text).toContain('allCountableTypeCodes')
    // flowgate.default.0423 T0005 item 10: a checked type is no longer hardcoded to 1
    // — it is left out of the request so the server can derive it (or fall back to 0).
    // Only an unchecked type still forces an explicit 0.
    expect(text).not.toContain('selectedTypes.value.has(code) ? 1 : 0')
    expect(text).toContain('.filter((code) => !selectedTypes.value.has(code))')
  })

  it('AI 범위 대화상자를 제공한다', () => {
    const path = resolve(process.cwd(), 'src/main/components/WorkPlanAiScopeDialog.vue')
    expect(existsSync(path)).toBe(true)
    if (!existsSync(path)) return
    const text = readFileSync(path, 'utf8')
    expect(text).toContain('quantity_type_codes')
    expect(text).toContain('step_keys')
    expect(text).toContain('provider_ids')
    expect(source('src/main/components/WorkPlanEditor.vue')).toContain('work_plan_fill')
  })
})
