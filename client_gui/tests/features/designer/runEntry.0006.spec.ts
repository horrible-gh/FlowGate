/**
 * flowgate.v02test.0001 0006-TR rev1 — the menu's 다음 단계 / 전체 실행 are the only run start: the Toolbar shows no
 * start button while idle (일시정지 / 재개 / 중지 stay while a run is on), and a document line has no [AI 호출].
 */
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import { slotLineCard } from '@/features/designer/inspector/parts/slotStateView'

registerAppMessages(i18n)

describe('Toolbar run buttons (0006-TR rev1)', () => {
  const mountToolbar = (runPhase: 'idle' | 'running' | 'paused' | 'stopping') =>
    mount(DesignerToolbar, { props: { runPhase, density: 'normal', zoom: 100 }, global: { plugins: [i18n] } })
  let wrapper: ReturnType<typeof mountToolbar> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('idle: no 다음 단계 / 전체 실행 start button (the menu starts a run)', () => {
    wrapper = mountToolbar('idle')
    expect(wrapper.find('[data-test="designer-tool-run"]').exists()).toBe(false)
    expect(wrapper.find('[data-command="run.next"], [data-command="run.all"]').exists()).toBe(false)
    expect(wrapper.find('[data-command="run.validate"]').exists()).toBe(true)
  })

  it.each([
    ['running', ['run.pause', 'run.stop']],
    ['paused', ['run.resume', 'run.stop']],
    ['stopping', ['run.stop']],
  ] as const)('%s: only the controls of the run that is on', (phase, commands) => {
    wrapper = mountToolbar(phase)
    expect(wrapper.findAll('[data-test="designer-tool-run"]').map((button) => button.attributes('data-command'))).toEqual(commands)
  })
})

describe('document line run action (0006-TR rev1)', () => {
  it('a line with a document offers no [AI 호출], changeable or running', () => {
    const card = { slot: 'report', docType: 'TR', docId: 'd' }
    expect(slotLineCard({ id: 'd', empty: false }, card, { changeable: true, running: () => false }).actions).toEqual([])
    expect(slotLineCard({ id: 'd', empty: false }, card, { changeable: true, running: () => true }).actions).toEqual([])
  })
})
