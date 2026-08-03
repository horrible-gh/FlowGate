// flowgate.default.0382 B0001 — "도구가 남긴 흔적"이 화면에 실제로 뜨는지.
//
// 이 사고가 통째로 지나갈 수 있었던 이유는 한 가지다: 261개가 **아무 화면에도 안 떴다**.
// 서버가 목록을 실어 보내도 화면이 안 그리면 같은 일이 반복되므로, 두 자리를 여기서
// 못박는다 — 변경사항 열람의 흔적 줄(제안 3)과 마무리 결과의 제외 줄(제안 1).
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount as vtuMount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'

// 두 컴포넌트 모두 자식이 pinia 스토어를 직접 잡는다. 스토어 자체는 이 검증의 대상이
// 아니므로, 진짜 pinia 하나를 붙여 마운트만 되게 한다.
function mount(component: unknown, options: Record<string, any> = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  return vtuMount(component as any, {
    ...options,
    global: { ...(options.global ?? {}), plugins: [pinia, ...(options.global?.plugins ?? [])] },
  })
}

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (k: string, params?: Record<string, unknown>) =>
      params ? `${k}:${JSON.stringify(params)}` : k,
  }),
}))

const { mockGet, mockPost, mockInvalidateProject, mockDashboardInvalidate } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockInvalidateProject: vi.fn(),
  mockDashboardInvalidate: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { get: mockGet, post: mockPost },
  getRequest: (...args: unknown[]) => mockGet(...args),
  postRequest: (...args: unknown[]) => mockPost(...args),
}))

vi.mock('../../stores/project', () => ({
  useProjectStore: () => ({ currentProjectId: 'p1' }),
}))
vi.mock('../../stores/explorer', () => ({
  useExplorerStore: () => ({
    invalidateProject: mockInvalidateProject,
    revealDocInGroupTree: vi.fn(),
  }),
}))
vi.mock('../../stores/dashboard', () => ({
  useDashboardStore: () => ({ invalidate: mockDashboardInvalidate }),
}))
vi.mock('../../stores/aiProvider', () => ({
  useAiProviderStore: () => ({
    providers: [], selectedProviderId: null, loading: false, error: null,
    selectProvider: vi.fn(), load: vi.fn(),
  }),
}))
vi.mock('../common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import GroupChangesDialog from '../GroupChangesDialog.vue'
import GitFinalizePanel from '../GitFinalizePanel.vue'
import { useFlowGateSse } from '../../composables/useFlowGateSse'

const ARTIFACTS = [
  'server/.test-tmp-0313/storage/a.json',
  'server/.test-tmp-0318/case-b/.git/objects/aa',
]

// 이 다이얼로그는 body 로 teleport 되므로, 스텁을 걸어야 wrapper 안에서 보인다.
function mountDialog(toolArtifacts: string[]) {
  return mount(GroupChangesDialog, {
    props: {
      projectId: 'p1',
      groupId: 'flowgate.default.0382',
      changes: [],
      toolArtifacts,
    },
    global: { stubs: { teleport: true } },
  })
}

describe('GroupChangesDialog — 도구가 남긴 흔적 줄 (0382 제안 3)', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
  })

  it('감춘 흔적의 개수를 접힌 한 줄로 보여준다', async () => {
    const wrapper = mountDialog(ARTIFACTS)
    await flushPromises()

    const row = wrapper.find('.gcd-artifacts')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('main.group_changes.tool_artifacts:{"n":2}')
    // 접혀 있는 동안에는 경로를 늘어놓지 않는다.
    expect(wrapper.find('.gcd-artifacts-list').exists()).toBe(false)
  })

  it('펼치면 어떤 경로가 빠졌는지 이름으로 확인된다', async () => {
    const wrapper = mountDialog(ARTIFACTS)
    await wrapper.find('.gcd-artifacts-toggle').trigger('click')

    const list = wrapper.find('.gcd-artifacts-list')
    expect(list.exists()).toBe(true)
    expect(list.text()).toContain('server/.test-tmp-0313/storage/a.json')
  })

  it('흔적이 없으면 줄 자체가 없다', async () => {
    const wrapper = mountDialog([])
    // 스텁이 실제로 걸려 화면이 그려졌는지부터 확인한다 — 아무것도 안 그려진 상태를
    // "줄이 없다"로 읽으면 이 검증은 거짓 초록이 된다.
    expect(wrapper.find('.gcd-blank').exists()).toBe(true)
    expect(wrapper.find('.gcd-artifacts').exists()).toBe(false)
  })
})

describe('GitFinalizePanel — 커밋에서 제외한 산출물 (0382 제안 1)', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    mockGet.mockResolvedValue({
      data: {
        ok: true,
        state: {
          group_id: 'g1', branch: 'wb', base_branch: 'main', status: 'waiting',
          default_action: 'merge', choices: ['merge'], ahead_count: 1,
          behind_count: 0, merge_id: null,
        },
      },
    })
  })

  it('마무리 결과가 제외 목록을 실어 오면 개수와 목록을 보여준다', async () => {
    const wrapper = mount(GitFinalizePanel, { props: { groupId: 'g1' } })
    await flushPromises()

    mockPost.mockResolvedValue({
      data: {
        ok: true,
        result: {
          action: 'merge', status: 'merged', merge_commit: 'abc1234', pushed: true,
          merge_id: null, conflict_files: [],
          excluded_artifact_count: 2,
          excluded_artifacts: ARTIFACTS,
        },
      },
    })
    await (wrapper.vm as any).postFinalize({ action: 'merge' }, false)
    await flushPromises()

    const row = wrapper.find('.git-fin-artifacts')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('main.git_finalize.excluded_artifacts:{"n":2}')

    await wrapper.find('.git-fin-artifacts-toggle').trigger('click')
    expect(wrapper.find('.git-fin-artifacts-list').text()).toContain(
      'server/.test-tmp-0313/storage/a.json',
    )
  })

  it('다른 실행 주체의 SSE 완료도 제외 목록을 화면에 남긴다', async () => {
    const wrapper = mount(GitFinalizePanel, { props: { groupId: 'g1' } })
    await flushPromises()

    window.dispatchEvent(new CustomEvent('fg:git_finalize_done', {
      detail: {
        group_id: 'g1',
        excluded_artifact_count: 2,
        excluded_artifacts: ARTIFACTS,
      },
    }))
    await flushPromises()

    expect(wrapper.find('.git-fin-artifacts').text()).toContain(
      'main.git_finalize.excluded_artifacts:{"n":2}',
    )
    await wrapper.find('.git-fin-artifacts-toggle').trigger('click')
    expect(wrapper.find('.git-fin-artifacts-list').text()).toContain(ARTIFACTS[0])
    wrapper.unmount()
  })

  it('제외한 것이 없으면 줄을 만들지 않는다', async () => {
    const wrapper = mount(GitFinalizePanel, { props: { groupId: 'g1' } })
    await flushPromises()

    mockPost.mockResolvedValue({
      data: {
        ok: true,
        result: {
          action: 'merge', status: 'merged', merge_commit: 'abc1234', pushed: true,
          merge_id: null, conflict_files: [],
          excluded_artifact_count: 0, excluded_artifacts: [],
        },
      },
    })
    await (wrapper.vm as any).postFinalize({ action: 'merge' }, false)
    await flushPromises()

    expect(wrapper.find('.git-fin-artifacts').exists()).toBe(false)
  })
})

describe('useFlowGateSse — 무인 마무리 결과 전달', () => {
  it('git_finalize_done payload의 제외 개수와 목록을 브라우저 이벤트로 전달한다', async () => {
    type Listener = (event: Event) => void
    class FakeEventSource {
      static latest: FakeEventSource | null = null
      listeners = new Map<string, Listener[]>()
      onopen: Listener | null = null
      onerror: Listener | null = null

      constructor(_url: string) {
        FakeEventSource.latest = this
      }

      addEventListener(name: string, listener: Listener) {
        const current = this.listeners.get(name) ?? []
        current.push(listener)
        this.listeners.set(name, current)
      }

      emit(name: string, data: unknown) {
        const event = new MessageEvent(name, { data: JSON.stringify(data) })
        for (const listener of this.listeners.get(name) ?? []) listener(event)
      }

      close() {}
    }

    vi.stubGlobal('EventSource', FakeEventSource)
    const refreshAll = vi.fn()
    const forwarded = vi.fn()
    window.addEventListener('fg:git_finalize_done', forwarded)
    const Harness = defineComponent({
      setup() {
        useFlowGateSse(refreshAll)
        return () => h('div')
      },
    })
    const wrapper = mount(Harness)
    await flushPromises()

    FakeEventSource.latest?.emit('git_finalize_done', {
      project: 'p1',
      payload: {
        group_id: 'g1',
        excluded_artifact_count: 2,
        excluded_artifacts: ARTIFACTS,
      },
    })

    expect(forwarded).toHaveBeenCalledTimes(1)
    const detail = (forwarded.mock.calls[0][0] as CustomEvent).detail
    expect(detail.group_id).toBe('g1')
    expect(detail.excluded_artifact_count).toBe(2)
    expect(detail.excluded_artifacts).toEqual(ARTIFACTS)

    wrapper.unmount()
    window.removeEventListener('fg:git_finalize_done', forwarded)
    vi.unstubAllGlobals()
  })
})
