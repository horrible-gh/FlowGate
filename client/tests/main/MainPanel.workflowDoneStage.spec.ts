import { describe, expect, it } from 'vitest'
import en from '@shared/i18n/en'
import ja from '@shared/i18n/ja'
import ko from '@shared/i18n/ko'

// R0001 group 0125 / NR0003 권고 2: the active-workflow stage badge gained a 'done' state.
// MainPanel.workflowStageLabel maps stage.state==='done' -> 'main.overview.workflow_done'.
// Guard the new user-facing string (and its {type} interpolation slot) across all locales so
// a finished head step renders a real label instead of a missing-key fallback.
describe('workflow_done stage label', () => {
  const locales: Array<[string, Record<string, any>]> = [
    ['ko', ko],
    ['ja', ja],
    ['en', en],
  ]

  for (const [name, messages] of locales) {
    it(`defines main.overview.workflow_done for ${name}`, () => {
      const value = messages.main?.overview?.workflow_done
      expect(typeof value).toBe('string')
      expect(value).toContain('{type}')
    })
  }
})
