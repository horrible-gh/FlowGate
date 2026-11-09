// flowgate.v02.0156 T#4 D8: a stopping run offers an enabled re-stop (the server's repeated Stop is idempotent),
// held back only while a command is in flight or the run is unknown.
import { describe, expect, it } from 'vitest'
import { ALL_MENU_TARGETS, isMenuCommandEnabled, nextRunPhase } from '../../../src/features/designer/top/designerTop'

describe('0156 D8: stop again on a stopping run', () => {
  it('enables run.stop while stopping, and only run.stop', () => {
    expect(isMenuCommandEnabled('run.stop', 'stopping', ALL_MENU_TARGETS)).toBe(true)
    for (const cmd of ['run.next', 'run.all', 'run.pause', 'run.resume'] as const) {
      expect(isMenuCommandEnabled(cmd, 'stopping', ALL_MENU_TARGETS)).toBe(false)
    }
  })

  it('keeps the busy and unknown holds', () => {
    expect(isMenuCommandEnabled('run.stop', 'stopping', { ...ALL_MENU_TARGETS, runBusy: true })).toBe(false)
    expect(isMenuCommandEnabled('run.stop', 'stopping', { ...ALL_MENU_TARGETS, runUnknown: true })).toBe(false)
  })

  it('still offers nothing to stop on an idle run', () => {
    expect(isMenuCommandEnabled('run.stop', 'idle', ALL_MENU_TARGETS)).toBe(false)
    expect(nextRunPhase('idle', 'run.stop')).toBe('idle')
  })
})
