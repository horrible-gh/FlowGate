import { describe, expect, it } from 'vitest'
import * as ProjectFeature from '@/features/project'

// D0004 §1.2/T#1 §6: App must only ever reach the store factory + domain types through
// this barrel — no component, API adapter, or persistence helper should be reachable here.
describe('features/project public surface', () => {
  it('exposes only the store factory at runtime', () => {
    expect(Object.keys(ProjectFeature)).toEqual(['useProjectStore'])
  })

  it('the exported store factory is callable', () => {
    expect(typeof ProjectFeature.useProjectStore).toBe('function')
  })
})
