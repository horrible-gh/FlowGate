import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  join(process.cwd(), 'src/main/components/WorkflowDecisionModal.vue'),
  'utf8',
)

function cssRule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))
  expect(match, `missing CSS rule for ${selector}`).not.toBeNull()
  return match?.[1] ?? ''
}

describe('WorkflowDecisionModal locked item layout', () => {
  it('caps the locked list and scrolls it independently', () => {
    const rule = cssRule('.wem-locked-list')

    expect(rule).toMatch(/max-height:\s*124px/)
    expect(rule).toMatch(/overflow-y:\s*auto/)
    expect(rule).toMatch(/overscroll-behavior:\s*contain/)
    expect(rule).toMatch(/scrollbar-gutter:\s*stable/)
  })

  it('uses a visible 8px scrollbar', () => {
    expect(cssRule('.wem-locked-list::-webkit-scrollbar')).toMatch(/width:\s*8px/)
    expect(cssRule('.wem-locked-list::-webkit-scrollbar-thumb')).toMatch(/background:\s*#94a3b8/)
  })
})
