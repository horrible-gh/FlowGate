import { describe, expect, it } from 'vitest'
import * as ProjectFeature from '@/features/project'

// D0004 §1.2/T#1 §6, T#2 §8: App must only ever reach the store factory + domain types +
// the top-level ProjectDialog component through this barrel — no ProjectList/ProjectRow/
// ProjectCreateDialog, API adapter, or persistence helper should be reachable here.
describe('features/project public surface', () => {
  it('exposes only the store factory and ProjectDialog at runtime', () => {
    expect(Object.keys(ProjectFeature)).toEqual(['useProjectStore', 'ProjectDialog'])
  })

  it('the exported store factory is callable', () => {
    expect(typeof ProjectFeature.useProjectStore).toBe('function')
  })

  it('exports ProjectDialog as a Vue component', () => {
    expect(ProjectFeature.ProjectDialog).toBeTruthy()
  })
})
