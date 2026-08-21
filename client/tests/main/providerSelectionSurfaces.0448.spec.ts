// 0448 T0005 §3 + §7-2 — the ten ORDINARY provider-selection surfaces.
//
// NR0003 §6-2: all ten converge on aiProviderStore.selectProvider, and that one function used
// to fold "the default for hops that stored nothing" together with "force this provider onto
// every step, permanently". Fixing the store alone is not proof: §3 says each event path has
// to be shown NOT to reach a force API and NOT to leave `pinned` true, and §7-2 says no row of
// the table may be replaced by reading the source. So every surface below is mounted and driven
// through the event a person actually fires, including the two indirect hops
// (ContinuousWorkDialog -> MainPanel and DocInfoPanel -> QaHistoryDialog).
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest, patchRequest, putRequest, deleteRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
  putRequest: vi.fn(),
  deleteRequest: vi.fn(),
  showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  patchRequest: (...a: unknown[]) => patchRequest(...a),
  putRequest: (...a: unknown[]) => putRequest(...a),
  deleteRequest: (...a: unknown[]) => deleteRequest(...a),
  serverLogout: vi.fn(),
  extractApiErrorMessage: (error: any, fallback: string) =>
    error?.response?.data?.error?.message ?? fallback,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

import { useAiProviderStore } from '@main/stores/aiProvider'
import AiProviderSelect from '@main/components/AiProviderSelect.vue'
import AppHeader from '@main/components/AppHeader.vue'
import ContinuousWarningDialog from '@main/components/ContinuousWarningDialog.vue'
import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'
import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import MainPanel from '@main/components/MainPanel.vue'
import ConversationView from '@main/components/ConversationView.vue'
import WorkPlanProposalDialog from '@main/components/WorkPlanProposalDialog.vue'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import QaHistoryDialog from '@main/components/QaHistoryDialog.vue'
import { useDocTypeStore } from '@main/stores/docTypeStore'
import { useProjectStore } from '@main/stores/project'

const PROJECT = 'flowgate'
const PROVIDERS = [
  { id: 'aip_default', name: 'Default Provider', exec_type: 'cli', kind: 'claude' },
  { id: 'aip_picked', name: 'Picked Provider', exec_type: 'cli', kind: 'claude' },
]
const SELECTION_KEY = `flowgate.user.guest.ai-provider.${PROJECT}`
const LEGACY_PIN_KEY = `flowgate.user.guest.ai-provider-pin.${PROJECT}`

function providersResponse() {
  return { data: { ok: true, project: PROJECT, providers: PROVIDERS, default_provider_id: 'aip_default' } }
}

/** Load the store the way the app does. */
async function armedStore() {
  const store = useAiProviderStore()
  await store.loadForProject(PROJECT)
  expect(store.selectedProviderId).toBe('aip_default')
  expect(store.pinned).toBe(false)
  return store
}

/** Plant the pre-0448 implicit-pin key immediately before the surface's own event, so each
 *  surface also proves its handler clears it instead of resurrecting it (§2-4). */
function plantLegacyPin() {
  localStorage.setItem(LEGACY_PIN_KEY, '1')
  expect(localStorage.getItem(LEGACY_PIN_KEY)).toBe('1')
}

/** What an ordinary pick — from ANY of the ten surfaces — is allowed to do. */
function expectOrdinarySelection(store: ReturnType<typeof useAiProviderStore>, forceSpy: ReturnType<typeof vi.spyOn>) {
  expect(store.selectedProviderId).toBe('aip_picked')
  expect(localStorage.getItem(SELECTION_KEY)).toBe('aip_picked')
  // The whole point of 0448: an ordinary pick is a DEFAULT, never a force-all.
  expect(store.pinned).toBe(false)
  expect(forceSpy).not.toHaveBeenCalled()
  expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  localStorage.clear()
  delete (window as any).__accessToken__
  for (const fn of [getRequest, postRequest, patchRequest, putRequest, deleteRequest, showToast]) fn.mockReset()
  getRequest.mockImplementation((url: string) =>
    url === '/api/v1/ai-invoke/providers'
      ? Promise.resolve(providersResponse())
      : Promise.resolve({ data: {} }),
  )
  postRequest.mockResolvedValue({ data: {} })
  patchRequest.mockResolvedValue({ data: {} })
})

afterEach(() => {
  document.body.innerHTML = ''
})

// Control for the twelve `expect(force).not.toHaveBeenCalled()` / `pinned === false`
// assertions above: the spy and the flag both react when the force API really is used, so a
// surface that quietly started forcing would be caught rather than passing silently.
describe('control: the force-all API is observable (0448 T0005 §7-3)', () => {
  it('control: calling forceProviderForAllSteps trips the spy and sets pinned', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')

    store.forceProviderForAllSteps('aip_picked')

    expect(force).toHaveBeenCalledWith('aip_picked')
    expect(store.pinned).toBe(true)
    expect(store.selectedProviderId).toBe('aip_picked')
  })
})

describe('ordinary provider selection — surfaces 1-4 (0448 T0005 §3)', () => {
  it('surface 1/10 AppHeader: the header select stores a default, not a pin', async () => {
    // AppHeader owns the "which project's providers" watcher; with no current project it
    // clears the store on mount and the selector has no options to pick from.
    useProjectStore().setCurrentProject(PROJECT)
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const wrapper = mount(AppHeader, {
      attachTo: document.body,
      global: { plugins: [i18n], stubs: { RouterLink: { template: '<a><slot /></a>' }, ProjectSelector: true } },
    })
    await flushPromises()

    plantLegacyPin()
    const select = wrapper.get('.ai-provider-selector select').element as HTMLSelectElement
    select.value = 'aip_picked'
    select.dispatchEvent(new Event('change'))
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })

  it('surface 2/10 ContinuousWarningDialog: the consent screen select stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const wrapper = mount(ContinuousWarningDialog, {
      props: { visible: true, project: PROJECT, stepCount: 2, targetLabel: 'report', reviewMode: false },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    plantLegacyPin()
    const select = document.querySelector('.cwarn-provider select') as HTMLSelectElement
    select.value = 'aip_picked'
    select.dispatchEvent(new Event('change'))
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })

  it('surface 3/10 AiInvokeDialog: the invoke-point select stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const wrapper = mount(AiInvokeDialog, {
      props: {
        visible: true, project: PROJECT, module: 'default', group: '0448',
        docRef: 'flowgate.default.0448.0005-T', actionScope: 'edit',
        continuationInstructionMode: 'auto_approved',
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    plantLegacyPin()
    await wrapper.findComponent(AiProviderSelect).vm.$emit('update:modelValue', 'aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })

  it('surface 4/10 WorkflowDecisionModal: the sequence-edit select stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/ai-invoke/providers'
        ? Promise.resolve(providersResponse())
        : Promise.resolve({ data: { items: [] } }),
    )
    const wrapper = mount(WorkflowDecisionModal, {
      props: { visible: true, mode: 'edit', docId: 'flowgate.default.0448.0001-B' },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()

    const select = wrapper.findComponent(AiProviderSelect)
    expect(select.exists()).toBe(true)
    plantLegacyPin()
    await select.vm.$emit('update:modelValue', 'aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })
})

describe('ordinary provider selection — surface 5, both hops (0448 T0005 §3-5)', () => {
  it('surface 5/10 hop A ContinuousWorkDialog: the default select only EMITS update:provider', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/ai-invoke/providers'
        ? Promise.resolve(providersResponse())
        : Promise.resolve({ data: {
            doc_id: 'flowgate.default.0448.0001-B', doc_class: 'B', decided: true,
            items: [{ id: 1, item_seq: 1, type: 'T', label: 'Work', status: 'pending',
                      provider_id: null, provider_display_name: null, provider_registered: null }],
            head: { id: 1, item_seq: 1, type: 'T', label: 'Work', status: 'pending' },
          } }),
    )
    const wrapper = mount(ContinuousWorkDialog, {
      props: {
        visible: true, docRef: 'flowgate.default.0448.0001-B',
        providers: PROVIDERS.map(p => ({ id: p.id, name: p.name })),
        selectedProvider: 'aip_default', providerPinned: false,
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    ;(document.querySelectorAll('.cwd-tab')[1] as HTMLButtonElement).click()
    await flushPromises()

    plantLegacyPin()
    const select = document.querySelector('.cwd-provider-select .aip-select-input') as HTMLSelectElement
    select.value = 'aip_picked'
    select.dispatchEvent(new Event('change'))
    await flushPromises()

    // The dialog owns no store: it hands the value to its parent and nothing else.
    expect(wrapper.emitted('update:provider')).toEqual([['aip_picked']])
    expect(force).not.toHaveBeenCalled()
    expect(store.pinned).toBe(false)
    wrapper.unmount()
  })

  it('surface 5/10 hop B MainPanel: the update:provider listener stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const wrapper = mount(MainPanel, { attachTo: document.body, shallow: true, global: { plugins: [i18n] } })
    await flushPromises()

    const dialog = wrapper.findComponent(ContinuousWorkDialog)
    expect(dialog.exists()).toBe(true)
    plantLegacyPin()
    await dialog.vm.$emit('update:provider', 'aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })
})

describe('ordinary provider selection — surfaces 6-9 (0448 T0005 §3)', () => {
  it('surface 6/10 ConversationView: the chat composer select stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const wrapper = mount(ConversationView, {
      props: { docId: 'flowgate.default.0448.0009-CH', projectId: PROJECT, readOnly: false },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    const select = wrapper.findComponent(AiProviderSelect)
    expect(select.exists()).toBe(true)
    plantLegacyPin()
    await select.vm.$emit('update:modelValue', 'aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })

  it('surface 7/10 WorkPlanProposalDialog: the proposal select stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const docTypeStore = useDocTypeStore()
    docTypeStore.items = [
      { id: 32, code: 'T', label: 'Work', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR' },
    ] as any
    docTypeStore.labelMap = { T: 'Work' }
    const wrapper = mount(WorkPlanProposalDialog, {
      props: {
        visible: true, parentDocId: 'flowgate.default.0448.0001-B',
        projectId: PROJECT, groupId: 'flowgate.default.0448',
      },
      global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
    })
    await flushPromises()

    const select = wrapper.findComponent(AiProviderSelect)
    expect(select.exists()).toBe(true)
    plantLegacyPin()
    await select.vm.$emit('update:modelValue', 'aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })

  it('surface 8/10 GitStatusPanel: the inline resolver select stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/ai-invoke/providers') return Promise.resolve(providersResponse())
      if (url.endsWith('/git/status')) {
        return Promise.resolve({ data: { ok: true, status: {
          enabled: true, base_branch: 'main', base_path_state: 'ready',
          ahead_count: 0, behind_count: 0, slots: [], pending_count: 1,
          pending: [{ group_id: 'flowgate.default.0448', branch: 'group/0448', status: 'conflict',
                      default_action: 'merge', merge_id: 7 }],
        } } })
      }
      if (url.includes('/git/merge/7/conflicts')) {
        return Promise.resolve({ data: { ok: true, files: [
          { path: 'a.txt', content: 'x\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> main\ny', conflict_count: 1 },
        ] } })
      }
      return Promise.resolve({ data: {} })
    })
    const wrapper = mount(GitStatusPanel, {
      props: { projectId: PROJECT },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
    })
    await flushPromises()
    await (wrapper.vm as any).openResolve('flowgate.default.0448')
    await flushPromises()

    const resolver = wrapper.findComponent(GitConflictResolverDialog)
    expect(resolver.exists()).toBe(true)
    plantLegacyPin()
    await resolver.vm.$emit('update:provider', 'aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })

  it('surface 9/10 GitFinalizePanel: the finalize resolver select stores a default, not a pin', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    useProjectStore().setCurrentProject(PROJECT)
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/ai-invoke/providers'
        ? Promise.resolve(providersResponse())
        : Promise.resolve({ data: { ok: true, state: {
            group_id: 'flowgate.default.0448', branch: 'group/0448', base_branch: 'main',
            status: 'awaiting_choice', default_action: 'merge_only', choices: ['merge_only', 'wait'],
            ahead_count: 1, behind_count: 0, merge_id: null,
            commit_message: { suggested: 'fix: work', source: 'auto' },
          } } }),
    )
    const wrapper = mount(GitFinalizePanel, {
      props: { groupId: 'flowgate.default.0448' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
    })
    await flushPromises()
    await (wrapper.vm as any).openConflictDialog()
    await flushPromises()

    const resolver = wrapper.findComponent(GitConflictResolverDialog)
    expect(resolver.exists()).toBe(true)
    plantLegacyPin()
    await resolver.vm.$emit('update:provider', 'aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })
})

describe('ordinary provider selection — surface 10, both hops (0448 T0005 §3-10)', () => {
  it('surface 10/10 hop A DocInfoPanel: it hands QaHistoryDialog the ordinary select API', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const wrapper = mount(DocInfoPanel, {
      shallow: true,
      props: {
        docId: 'flowgate.default.0448.0005-T', typeCode: 'T', reviewStatus: 'wf_in_progress',
        rejectReason: null, stepStates: [], nextStepIndex: null, collapsed: false,
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    const qa = wrapper.findComponent(QaHistoryDialog)
    expect(qa.exists()).toBe(true)
    const handedOver = qa.props('selectProvider') as (id: string) => void
    // Not "some function": the very function the store exposes as its ordinary contract.
    expect(handedOver).toBe(store.selectProvider)
    plantLegacyPin()
    handedOver('aip_picked')
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })

  it('surface 10/10 hop B QaHistoryDialog: its own select calls that prop, and nothing else', async () => {
    const store = await armedStore()
    const force = vi.spyOn(store, 'forceProviderForAllSteps')
    const wrapper = mount(QaHistoryDialog, {
      props: {
        visible: true, docId: 'flowgate.default.0448.0005-T', busy: false,
        items: [{ id: 7, seq: 1, title: 'q', body: 'b', asker_kind: 'ai', answer_count: 0, answers: [] }],
        submitAnswer: vi.fn(), requestAiAnswer: vi.fn(),
        aiProviders: PROVIDERS.map(p => ({ id: p.id, name: p.name })),
        selectedProviderId: 'aip_default',
        selectProvider: store.selectProvider,
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    const select = document.body.querySelector('.qhd-provider-select select') as HTMLSelectElement
    expect(select).toBeTruthy()
    plantLegacyPin()
    select.value = 'aip_picked'
    select.dispatchEvent(new Event('change'))
    await flushPromises()

    expectOrdinarySelection(store, force)
    wrapper.unmount()
  })
})
