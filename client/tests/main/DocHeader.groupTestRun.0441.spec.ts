// flowgate.default.0441 TR0005 rev4 — DocHeader preserves unknown group-run state until detail loads.
//
// Rejection: "테스트 중일떄는 \"그룹 내 다른 문서의\" 액션바\"도\" 전부 비활성화 해놔야 할거아냐".
//
// The action bar can only lock every document of the group if every document is TOLD a run is
// in flight. `test_run` on the detail payload is bound to the fetched document, so it is null
// on a sibling; the server now also ships `group_test_run`, computed from the group. This is
// the bridge between the two — the exposed value MainPanel forwards as `group-test-run-active`
// (MainPanel.groupTestRunLock.0441.spec.ts) and the bar locks on (ReviewActionBar.spec.ts L14+).

import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

// The TR sibling of the TS the test is running on — its own test_run embed is always null.
const TAB = { id: 'test.none.0441.0005-TR', title: 'TR', path: '', type: 'md', typeCode: 'TR' }

let groupTestRun: unknown

function detailResponse() {
  const data: Record<string, unknown> = {
    doc_id: TAB.id,
    title: 'TR',
    status: 'open',
    type_code: 'TR',
    doc_review_status: 'pending_review',
    is_editable: true,
    project_id: 'test',
    group_id: 'test.none.0441',
    workflow_steps: ['TS', 'TSR'],
    test_run: null,
  }
  // `undefined` models an older server that does not send the block at all.
  if (groupTestRun !== undefined) data.group_test_run = groupTestRun
  return { data }
}

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  groupTestRun = undefined
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader group_test_run (0441 rev4)', () => {
  it('exposes groupTestRunActive=true for a run on ANOTHER document, while its own testRun stays null', async () => {
    groupTestRun = {
      active: true,
      run_id: 'trun_20260826_000001',
      doc_id: 'test.none.0441.0004-TS',
      status: 'running',
    }
    const wrapper = mountHeader()
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.testRun).toBeNull()
    expect(vm.groupTestRunActive).toBe(true)
    wrapper.unmount()
  })

  it('positive control: an inactive block exposes false', async () => {
    groupTestRun = { active: false, run_id: null, doc_id: null, status: null }
    const wrapper = mountHeader()
    await flushPromises()

    expect((wrapper.vm as any).groupTestRunActive).toBe(false)
    wrapper.unmount()
  })

  it('a cancelling run still counts as active (the kill has not landed yet)', async () => {
    groupTestRun = { active: true, run_id: 'trun_x', doc_id: 'test.none.0441.0004-TS', status: 'cancelling' }
    const wrapper = mountHeader()
    await flushPromises()

    expect((wrapper.vm as any).groupTestRunActive).toBe(true)
    wrapper.unmount()
  })

  it('stays unknown before the detail response instead of briefly unlocking a newly opened sibling', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) return new Promise(() => {})
      if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
      return Promise.resolve({ data: {} })
    })
    const wrapper = mountHeader()
    await wrapper.vm.$nextTick()

    expect((wrapper.vm as any).groupTestRunActive).toBeUndefined()
    wrapper.unmount()
  })

  it('a payload with no group_test_run key stays unknown so MainPanel fails closed', async () => {
    groupTestRun = undefined
    const wrapper = mountHeader()
    await flushPromises()

    expect((wrapper.vm as any).groupTestRunActive).toBeUndefined()
    wrapper.unmount()
  })
})
