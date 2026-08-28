import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  resolve(process.cwd(), 'src/main/components/MainPanel.vue'),
  'utf8',
)

describe('MainPanel step-verification refresh after content save (0467 T0012)', () => {
  it('binds each TR card instance into the tab-keyed registry', () => {
    expect(source).toContain('const stepVerificationCardRefs = reactive<Record<string, any>>({})')
    expect(source).toContain(':ref="(el) => bindActiveRef(stepVerificationCardRefs, tab.id, el)"')
  })

  it('refreshes only after PATCH succeeds and never from the catch path', () => {
    const start = source.indexOf('async function saveEditContent()')
    const end = source.indexOf('\nconst activeProjects', start)
    const body = source.slice(start, end)
    const patch = body.indexOf('await patchRequest(')
    const refresh = body.indexOf('await stepVerificationCardRefs[editTab.value.id]?.fetchData?.()')
    const catchBlock = body.indexOf('} catch (e: any) {')

    expect(patch).toBeGreaterThan(-1)
    expect(refresh).toBeGreaterThan(patch)
    expect(catchBlock).toBeGreaterThan(refresh)
    expect(body.slice(catchBlock)).not.toContain('stepVerificationCardRefs')
  })
})
