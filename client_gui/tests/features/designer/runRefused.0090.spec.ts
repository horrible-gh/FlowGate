import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import { ALL_MENU_TARGETS, isMenuCommandEnabled } from '@/features/designer/top/designerTop'

registerAppMessages(i18n)
const toolbar = (props: Record<string, unknown>) => mount(DesignerToolbar, {
  props: { density: 'normal', zoom: 100, targets: ALL_MENU_TARGETS, runPhase: 'idle', ...props },
  global: { plugins: [i18n] },
})
const runButtons = (w: ReturnType<typeof toolbar>) => w.findAll('[data-test="designer-tool-run"]')

describe('the Toolbar run buttons follow a refused project.run', () => {
  it('allowed (or not answered yet): both run buttons are on, no tooltip', () => {
    const w = toolbar({})
    expect(runButtons(w).map((b) => b.attributes('disabled'))).toEqual([undefined, undefined])
    expect(runButtons(w).map((b) => b.attributes('title'))).toEqual([undefined, undefined])
  })

  it('refused: both are off and the tooltip is the server\'s reason sentence', () => {
    const w = toolbar({ runRefused: true, runRefusedReason: '이 프로젝트에서 보기 전용 역할이라 할 수 없습니다(개발자 이상 필요).' })
    expect(runButtons(w).map((b) => b.attributes('disabled') !== undefined)).toEqual([true, true])
    expect(runButtons(w).map((b) => b.attributes('title'))).toEqual([expect.stringContaining('개발자 이상 필요'), expect.stringContaining('개발자 이상 필요')])
  })

  it('a running run keeps its ■ 중지 button on even when project.run is refused (pause is off)', () => {
    const w = toolbar({ runPhase: 'running', runRefused: true, runRefusedReason: 'x' })
    const stop = runButtons(w).find((b) => b.attributes('data-command') === 'run.stop')
    expect(stop).toBeTruthy()
    expect(stop!.attributes('disabled')).toBeUndefined()
    expect(stop!.attributes('title')).toBeUndefined()
    const pause = runButtons(w).find((b) => b.attributes('data-command') === 'run.pause')
    expect(pause!.attributes('disabled')).toBeDefined()
  })

  it('Click on a refused run button emits nothing', async () => {
    const w = toolbar({ runRefused: true, runRefusedReason: 'x' })
    await runButtons(w)[0]!.trigger('click')
    expect(w.emitted('command')).toBeUndefined()
  })
})

describe('the Run menu follows the same refusal (isMenuCommandEnabled)', () => {
  const refused = { ...ALL_MENU_TARGETS, runRefused: true }
  it('every run command but Stop is off for a refused person; edit / file commands are not touched', () => {
    for (const cmd of ['run.next', 'run.all', 'run.pause', 'run.resume'] as const) {
      expect(isMenuCommandEnabled(cmd, 'idle', ALL_MENU_TARGETS) || isMenuCommandEnabled(cmd, 'running', ALL_MENU_TARGETS) || isMenuCommandEnabled(cmd, 'paused', ALL_MENU_TARGETS)).toBe(true)
      for (const phase of ['idle', 'running', 'paused'] as const) expect(isMenuCommandEnabled(cmd, phase, refused)).toBe(false)
    }
    expect(isMenuCommandEnabled('run.stop', 'running', refused)).toBe(true)
    expect(isMenuCommandEnabled('edit.select-all', 'idle', refused)).toBe(true)
  })
})
