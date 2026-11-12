import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// flowgate.v02gui.0003 — the AI picker popover opens at the composer's left content edge in a
// ~300px panel; a 360px-wide popover (long provider name) ran ~69px past the screen edge. jsdom has
// no layout, so these source guards keep the popover capped to the composer's width (100cqw).
const CONV = resolve(__dirname, '../../../src/features/conversation')
const styleOf = (file: string) => readFileSync(join(CONV, file), 'utf8').split('<style')[1] ?? ''
const rule = (css: string, selector: string) => {
  const at = css.indexOf(`${selector} {`)
  expect(at, selector).toBeGreaterThanOrEqual(0)
  return css.slice(at, css.indexOf('}', at))
}

describe('composer AI picker popover width (flowgate.v02gui.0003)', () => {
  it('makes the composer an inline-size container', () => {
    expect(rule(styleOf('ConversationComposer.vue'), '.conv-compose')).toMatch(/container-type:\s*inline-size;/)
  })

  it('caps the popover to the composer width, border included', () => {
    const pop = rule(styleOf('ComposerModelPicker.vue'), '.cmp-model-pop')
    expect(pop).toMatch(/left:\s*0;/)
    expect(pop).toMatch(/box-sizing:\s*border-box;/)
    expect(pop).toMatch(/max-width:\s*min\(360px,\s*100cqw\);/)
    expect(pop).toMatch(/min-width:\s*min\(220px,\s*100cqw\);/)
    expect(pop).not.toMatch(/vw\)/)
  })
})
