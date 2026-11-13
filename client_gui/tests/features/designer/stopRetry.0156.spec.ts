// v02gui.0013: Stop progress is disabled; a failed request can explicitly offer an idempotent retry.
import { describe, expect, it } from 'vitest'
import { ALL_MENU_TARGETS, isMenuCommandEnabled, nextRunPhase } from '../../../src/features/designer/top/designerTop'

describe('0156 D8: stop again on a stopping run', () => {
  it('enables only an explicit retry while stopping', () => {
    expect(isMenuCommandEnabled('run.stop', 'stopping', ALL_MENU_TARGETS)).toBe(false)
    expect(isMenuCommandEnabled('run.stop', 'stopping', { ...ALL_MENU_TARGETS, runStopRetry: true })).toBe(true)
    for (const cmd of ['run.next', 'run.all', 'run.pause', 'run.resume'] as const) {
      expect(isMenuCommandEnabled(cmd, 'stopping', ALL_MENU_TARGETS)).toBe(false)
    }
  })

  it('keeps the busy and unknown holds', () => {
    expect(isMenuCommandEnabled('run.stop', 'stopping', { ...ALL_MENU_TARGETS, runBusy: true, runStopRetry: true })).toBe(false)
    expect(isMenuCommandEnabled('run.stop', 'stopping', { ...ALL_MENU_TARGETS, runUnknown: true, runStopRetry: true })).toBe(false)
  })

  it('still offers nothing to stop on an idle run', () => {
    expect(isMenuCommandEnabled('run.stop', 'idle', ALL_MENU_TARGETS)).toBe(false)
    expect(nextRunPhase('idle', 'run.stop')).toBe('idle')
  })
})
