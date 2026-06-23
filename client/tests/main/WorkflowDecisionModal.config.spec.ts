import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// R0001 (group 0067) "워크플로 결정 다이얼로그 정리":
//  ① V/VR 제거  ③④ 프리셋 재정의 (자동 보고서는 buildEntries 가 AUTO_MAP 으로 삽입)
// 이 스펙은 정적 설정 블록을 소스 텍스트로 검증한다 (layout.spec.ts 와 동일 패턴).

const source = readFileSync(
  join(process.cwd(), 'src/main/components/WorkflowDecisionModal.vue'),
  'utf8',
)

describe('WorkflowDecisionModal static config — R0001 cleanup', () => {
  it('① no V/VR anywhere in the component config', () => {
    // V is no longer a picker action item; VR is fully retired.
    expect(source).not.toMatch(/type:\s*'V'/)
    expect(source).not.toMatch(/'VR'/)
    expect(source).not.toMatch(/auto_only_hint_VR/)
  })

  it('① AUTO_MAP keeps only N/T/TS auto reports', () => {
    expect(source).toMatch(/AUTO_MAP[^=]*=\s*\{\s*N:\s*\['NR'\],\s*T:\s*\['TR'\],\s*TS:\s*\['TSR'\],?\s*\}/)
    expect(source).toMatch(/AUTO_TYPES\s*=\s*new Set\(\['NR',\s*'TR',\s*'TSR'\]\)/)
  })

  it('① C action item is preserved', () => {
    expect(source).toMatch(/type:\s*'C'/)
  })

  it('CH conversation type is selectable in the picker (TR0044.0010 rev1)', () => {
    // R0044.0001 rejection: CH did not appear in the workflow-decision /
    // sequence-edit dialogs, so the new type could not be confirmed. It must
    // be a pickable category item, like its general-series sibling M.
    expect(source).toMatch(/key:\s*'conversation'/)
    expect(source).toMatch(/type:\s*'CH'/)
  })

  it('③④ presets redefined without V (auto reports added by buildEntries)', () => {
    // 간소화 → N (NR) T (TR)  [R0120.0001]
    expect(source).toMatch(/preset_simple',\s*types:\s*\['N',\s*'T'\]/)
    // 버그수정 → N (NR) T (TR) TS (TSR)  [R0120.0001]
    expect(source).toMatch(/preset_bugfix',\s*types:\s*\['N',\s*'T',\s*'TS'\]/)
    // 설계만 → DS D P L DB
    expect(source).toMatch(/preset_design',\s*types:\s*\['DS',\s*'D',\s*'P',\s*'L',\s*'DB'\]/)
    // 표준 풀 사이클 → DS D P L DB T (TR) TS (TSR)
    expect(source).toMatch(/preset_standard',\s*types:\s*\['DS',\s*'D',\s*'P',\s*'L',\s*'DB',\s*'T',\s*'TS'\]/)
  })
})
