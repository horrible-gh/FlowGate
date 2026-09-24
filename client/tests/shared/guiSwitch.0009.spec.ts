import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getStoredGuiGeneration,
  setStoredGuiGeneration,
  switchGuiGeneration,
} from '@shared/guiSwitch'

// T0009 §4/§3: fg_ui is the single localStorage key remembering the last GUI-generation
// choice, and switchGuiGeneration performs the full-page navigation a switch requires
// (v0.1 and v0.2 are independent SPAs, not one client-side router).
describe('guiSwitch', () => {
  beforeEach(() => {
    localStorage.clear()
    // jsdom's real Location throws "not implemented" on navigation; swap in a plain
    // object so `.assign` is observable instead of erroring the test (mirrors the
    // `.replace` swap client/tests/stores/authRedirect.returnPath.0005.spec.ts uses).
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { assign: (url: string) => void } }).location = {
      assign: vi.fn(),
    }
  })

  it('returns null when nothing is stored', () => {
    expect(getStoredGuiGeneration()).toBeNull()
  })

  it('round-trips a valid stored value', () => {
    setStoredGuiGeneration('app')
    expect(localStorage.getItem('fg_ui')).toBe('app')
    expect(getStoredGuiGeneration()).toBe('app')

    setStoredGuiGeneration('legacy')
    expect(localStorage.getItem('fg_ui')).toBe('legacy')
    expect(getStoredGuiGeneration()).toBe('legacy')
  })

  it('ignores an arbitrary stored value instead of returning it (T0009 §4: "다른 임의 값은 저장하지 않는다")', () => {
    localStorage.setItem('fg_ui', 'something-else')
    expect(getStoredGuiGeneration()).toBeNull()
  })

  it('switching to app stores "app" and navigates full-page to /app', () => {
    switchGuiGeneration('app')
    expect(localStorage.getItem('fg_ui')).toBe('app')
    expect(window.location.assign).toHaveBeenCalledWith('/app')
  })

  it('switching to legacy stores "legacy" and navigates full-page to /main', () => {
    switchGuiGeneration('legacy')
    expect(localStorage.getItem('fg_ui')).toBe('legacy')
    expect(window.location.assign).toHaveBeenCalledWith('/main')
  })
})
