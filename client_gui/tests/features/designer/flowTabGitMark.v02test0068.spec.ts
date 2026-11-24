/** flowgate.v02test.0068: a Flow's Git run shows on its tab — turning while it runs, then done / skipped / stopped. */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'
import {
  clearGitTabResult, gitTabActivity, gitTabActivityLabel, pruneGitTabActivity, setActiveGitTab, setGitTabActivity,
} from '@/features/designer/center/gitTabActivity'

registerAppMessages(i18n)
let wrapper: VueWrapper<any> | null = null
afterEach(() => {
  wrapper?.unmount(); wrapper = null
  pruneGitTabActivity(new Set())
  i18n.global.locale.value = 'ko'
})

describe('Git activity on the Flow tab (flowgate.v02test.0068)', () => {
  it('keeps a result until the tab is activated again, never a running mark', () => {
    setGitTabActivity('a', { state: 'running', step: 'merge', code: null })
    clearGitTabResult('a')
    expect(gitTabActivity.a?.state).toBe('running')
    setGitTabActivity('a', { state: 'failed', step: 'merge', code: 'merge_conflict' })
    clearGitTabResult('a')
    expect(gitTabActivity.a).toBeUndefined()
  })

  it('keeps no result on the shown tab: it goes the moment the run ends there', () => {
    setActiveGitTab('a')
    setGitTabActivity('a', { state: 'running', step: 'push', code: null })
    setGitTabActivity('b', { state: 'running', step: 'push', code: null })
    expect(gitTabActivity.a?.state).toBe('running')
    setGitTabActivity('a', { state: 'done', step: null, code: null })
    setGitTabActivity('b', { state: 'failed', step: 'push', code: 'push_rejected' })
    expect(gitTabActivity.a).toBeUndefined()
    expect(gitTabActivity.b?.state).toBe('failed')
    // Another tab keeps its result until it is activated.
    setActiveGitTab('b')
    expect(gitTabActivity.b).toBeUndefined()
  })

  it('forgets the shown tab once it is closed', () => {
    setActiveGitTab('a')
    pruneGitTabActivity(new Set(['b']))
    setGitTabActivity('a', { state: 'done', step: null, code: null })
    expect(gitTabActivity.a?.state).toBe('done')
  })

  it('drops the marks of closed tabs', () => {
    setGitTabActivity('a', { state: 'done', step: null, code: null })
    setGitTabActivity('b', { state: 'done', step: null, code: null })
    pruneGitTabActivity(new Set(['b']))
    expect(Object.keys(gitTabActivity)).toEqual(['b'])
  })

  it('says the state, the step and the code on hover', () => {
    expect(gitTabActivityLabel({ state: 'failed', step: 'merge', code: 'merge_conflict' }, 'ko')).toBe('Git 멈춤 · 병합 · 코드: merge_conflict')
    expect(gitTabActivityLabel({ state: 'skipped', step: 'update', code: 'git_busy' }, 'en')).toBe('Git done · update skipped · Update · code: git_busy')
    expect(gitTabActivityLabel({ state: 'running', step: null, code: null }, 'ja')).toBe('Git 実行中')
  })

  it.each([['running'], ['done'], ['skipped'], ['failed']] as const)('draws the %s mark it is given on the tab', (state) => {
    wrapper = mount(FlowTabStrip, {
      props: { tabs: [{ flowId: 'a', name: '깃' }, { flowId: 'b', name: '선' }], activeFlowId: 'a',
        gitActivity: { a: { state, step: 'push', code: state === 'failed' ? 'push_rejected' : null } } },
      global: { plugins: [i18n] },
    })
    const [first, second] = wrapper.findAll('[data-test="designer-flow-tab-button"]')
    const mark = first.get('[data-test="designer-flow-tab-git"]')
    expect(mark.attributes('data-git-state')).toBe(state)
    expect(mark.classes()).toContain(`is-${state}`)
    expect(mark.attributes('role')).toBe('img')
    expect(mark.attributes('title')).toContain('푸시')
    expect(first.text()).toBe('깃')
    expect(second.find('[data-test="designer-flow-tab-git"]').exists()).toBe(false)
  })
})
