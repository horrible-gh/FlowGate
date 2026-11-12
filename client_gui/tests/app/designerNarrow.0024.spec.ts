import { describe, expect, it } from 'vitest'
import appSource from '@/app/App.vue?raw'
import shellSource from '@/features/designer/shell/DesignerShell.vue?raw'

// flowgate.v02test.0024: at <=1120px the App Shell lets ordinary screens scroll as a page, but the
// full-screen Designer must stay pinned to the viewport. Otherwise the right-dock Conversation has
// no definite height, its timeline loses its own scroll and the composer is pushed off screen.
// jsdom does no layout, so this pins the CSS source the browser check was done against.
function narrowBlock(source: string): string {
  const start = source.indexOf('@media (max-width: 1120px)')
  expect(start).toBeGreaterThan(-1)
  let depth = 0
  for (let i = source.indexOf('{', start); i < source.length; i++) {
    if (source[i] === '{') depth++
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1)
  }
  throw new Error('unbalanced @media block')
}

describe('Designer at <=1120px (flowgate.v02test.0024)', () => {
  const block = narrowBlock(appSource)

  it('App.vue keeps the full-screen shell pinned to the viewport', () => {
    expect(block).toMatch(/\.gui-app-shell\.is-fullscreen\s*\{[^}]*height:\s*100vh;[^}]*overflow:\s*hidden;/)
    expect(block).toMatch(/\.gui-app-shell\.is-fullscreen \.gui-route-content\s*\{[^}]*overflow:\s*hidden;/)
  })

  it('ordinary screens still scroll as a page', () => {
    expect(block).toMatch(/\.gui-app-shell\s*\{[^}]*height:\s*auto;[^}]*overflow:\s*visible;/)
  })

  it('DesignerShell no longer forces a page-height minimum at that width', () => {
    expect(shellSource).not.toMatch(/@media \(max-width: 1120px\)/)
  })
})
