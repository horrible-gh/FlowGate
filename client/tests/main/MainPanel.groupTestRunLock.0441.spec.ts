// flowgate.default.0441 TR0005 rev2 — the group-wide test-run lock reaches the action bar.
//
// Rejection: "테스트 중일떄는 \"그룹 내 다른 문서의\" 액션바\"도\" 전부 비활성화 해놔야 할거아냐".
//
// rev1 locked the bar from `props.testRunStatus`, which MainPanel fills from the ACTIVE tab's
// own `test_run` embed. On any sibling document of the same group that embed is null, so the
// bar came back to life the moment the user switched tabs mid-run. The server now ships a
// group-scoped `group_test_run` block on every document detail; DocHeader exposes its
// `active` flag and MainPanel forwards it here as `group-test-run-active`.
//
// ReviewActionBar.spec.ts (L14-L18) pins what the bar DOES with the flag. This file pins the
// wiring in between — that the value actually arrives, on a document that is NOT the one the
// test is running on.

import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { expectDocumentBranchMounted, mountMainPanel } from '../helpers/mountMainPanel'

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: { questions: [] } }),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({
    issueToken: vi.fn(),
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

const GROUP = 'test.test.0441'
// The document the reviewer is looking at. It is NOT the TS the test is running on.
const SIBLING_TAB = {
  id: 'test.test.0441.0005-TR',
  title: 'TR doc',
  path: '',
  type: 'md' as const,
  typeCode: 'TR',
}

/**
 * A DocHeader stand-in with the real component's exposed surface for this binding.
 *
 * MainPanel re-binds `docHeaderRefs[tabId]` to the mounted DocHeader on every render, so
 * seeding that registry by hand from the test is overwritten before the action bar reads it.
 * Exposing from the stub is the honest route: `groupTestRunActive` is precisely what
 * DocHeader computes from the detail payload (`doc.value?.group_test_run?.active === true`),
 * and `testRun` stays null because THIS document has no run of its own — the state rev1
 * could not lock.
 */
function fakeDocHeader(groupTestRunActive: boolean | undefined) {
  return defineComponent({
    name: 'DocHeader',
    props: { tab: { type: Object, default: null } },
    setup(_props, { expose }) {
      const exposed: Record<string, unknown> = {
        docTypeCode: 'TR',
        docProjectId: 'test',
        docModule: 'test',
        groupId: GROUP,
        docReviewStatus: 'pending_review',
        docLoaded: true,
        testRun: null,
      }
      // The third case below omits the key entirely — a header that has not answered yet.
      if (groupTestRunActive !== undefined) exposed.groupTestRunActive = groupTestRunActive
      expose(exposed)
      return () => h('div', { class: 'doc-header-fake' })
    },
  })
}

async function mountWith(groupTestRunActive: boolean | undefined) {
  return mountMainPanel({
    tabs: [SIBLING_TAB],
    activeTabId: SIBLING_TAB.id,
    stubs: { DocHeader: fakeDocHeader(groupTestRunActive) },
  })
}

function actionBarProps(wrapper: any) {
  const bar = wrapper.findComponent({ name: 'ReviewActionBar' })
  expect(bar.exists()).toBe(true)
  return bar.props() as Record<string, unknown>
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete (window as any).__accessToken__
  getRequest.mockClear()
})

describe('0441 MainPanel: the group-wide test-run lock reaches a sibling action bar', () => {
  it('a run elsewhere in the group forwards groupTestRunActive=true while this tab own testRunStatus stays null', async () => {
    const wrapper = await mountWith(true)
    expectDocumentBranchMounted(wrapper)
    await wrapper.vm.$nextTick()

    const props = actionBarProps(wrapper)
    // Both halves matter: the per-document channel is silent (rev1 saw only this), and the
    // group channel is the one carrying the lock.
    expect(props.testRunStatus).toBeNull()
    expect(props.groupTestRunActive).toBe(true)

    wrapper.unmount()
  })

  it('positive control: with no run anywhere in the group the same binding is false', async () => {
    const wrapper = await mountWith(false)
    expectDocumentBranchMounted(wrapper)
    await wrapper.vm.$nextTick()

    expect(actionBarProps(wrapper).groupTestRunActive).toBe(false)

    wrapper.unmount()
  })

  it('an unanswered sibling header fails closed, so its action bar stays locked until detail explicitly reports idle', async () => {
    const wrapper = await mountWith(undefined)
    expectDocumentBranchMounted(wrapper)
    await wrapper.vm.$nextTick()

    // rev2 used `?? false` here. Switching from the running TS to a sibling therefore
    // revived that sibling bar while its detail request was in flight (and forever if
    // the request failed). Unknown must be busy; an answered idle payload above is the
    // only state that unlocks it.
    expect(actionBarProps(wrapper).groupTestRunActive).toBe(true)

    wrapper.unmount()
  })
})
