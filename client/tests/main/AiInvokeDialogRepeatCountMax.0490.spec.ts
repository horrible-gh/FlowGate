import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'
import { useAiProviderStore } from '@main/stores/aiProvider'

// 0490 T0007 §6: proves the review-loop repeat-count selects actually move with the server's
// ai_repeat_count_max ceiling (via aiProviderStore.executionPolicy) instead of the old fixed
// [-1,1,2,3] / [-1,0,1,2] arrays, and that the review-count DEFAULT clamps when the ceiling
// drops below the historical literal 3 (§3.5). Distinct from the protected
// client/tests/main/AiInvokeDialog.spec.ts, which stays on the DEFAULT (3) ceiling throughout.

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

const PROVIDERS = [
  { id: 'reviewer', name: 'Reviewer AI', exec_type: 'cli', kind: 'codex', enabled: true },
  { id: 'reworker', name: 'Reworker AI', exec_type: 'cli', kind: 'codex', enabled: true },
]

function providersResponse(executionPolicy?: Record<string, number>) {
  const data: Record<string, unknown> = {
    ok: true,
    project: 'flowgate',
    default_provider_id: 'reviewer',
    providers: PROVIDERS,
  }
  if (executionPolicy) data.execution_policy = executionPolicy
  return { data }
}

function mountDialog() {
  return mount(AiInvokeDialog, {
    props: {
      visible: true,
      project: 'flowgate',
      module: 'default',
      group: '0490',
      docRef: 'flowgate.default.0490.0003-T',
      actionScope: 'review',
      continuationInstructionMode: 'auto_approved',
    },
    global: { plugins: [i18n] },
  })
}

async function pickLoop() {
  const radio = document.querySelector('input[type="radio"][value="loop"]') as HTMLInputElement
  radio.checked = true
  radio.dispatchEvent(new Event('change'))
  await flushPromises()
}

async function openTab(tab: 'review' | 'rework' | 'stop') {
  ;(document.querySelector(`[data-test="review-loop-tab-${tab}"]`) as HTMLButtonElement).click()
  await flushPromises()
}

function optionValues(query: string): string[] {
  return Array.from(document.querySelectorAll(query)).map((node) => (node as HTMLOptionElement).value)
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
  localStorage.clear()
  window.__accessToken__ = undefined
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('AiInvokeDialog repeat-count selects follow the server ceiling (0490 T0007)', () => {
  it('renders 1..5 (+sentinel) for both selects when execution_policy.repeat_count_max is 5', async () => {
    getRequest.mockResolvedValue(
      providersResponse({ repeat_count_max: 5, repeat_count_min: 1, repeat_count_hard_max: 30 }),
    )
    await useAiProviderStore().loadForProject('flowgate')
    mountDialog()
    await flushPromises()
    await pickLoop()

    expect(optionValues('[data-test="review-loop-review-count"] option')).toEqual(['-1', '1', '2', '3', '4', '5'])

    await openTab('stop')
    expect(optionValues('[data-test="review-loop-failure-restart"] option')).toEqual([
      '-1', '0', '1', '2', '3', '4', '5',
    ])
  })

  it('renders 1..10 (+sentinel) when execution_policy.repeat_count_max is 10', async () => {
    getRequest.mockResolvedValue(
      providersResponse({ repeat_count_max: 10, repeat_count_min: 1, repeat_count_hard_max: 30 }),
    )
    await useAiProviderStore().loadForProject('flowgate')
    mountDialog()
    await flushPromises()
    await pickLoop()

    expect(optionValues('[data-test="review-loop-review-count"] option')).toEqual([
      '-1',
      ...Array.from({ length: 10 }, (_, i) => String(i + 1)),
    ])
  })

  it('clamps the review-count DEFAULT to the ceiling instead of leaving an unreachable literal 3', async () => {
    getRequest.mockResolvedValue(
      providersResponse({ repeat_count_max: 1, repeat_count_min: 1, repeat_count_hard_max: 30 }),
    )
    await useAiProviderStore().loadForProject('flowgate')
    mountDialog()
    await flushPromises()
    await pickLoop()

    expect(optionValues('[data-test="review-loop-review-count"] option')).toEqual(['-1', '1'])
    const select = document.querySelector('[data-test="review-loop-review-count"]') as HTMLSelectElement
    // Not '3': that value has no matching <option> once the ceiling drops below it.
    expect(select.value).toBe('1')
  })

  it('falls back to the historical 1..3 ceiling when the response carries no execution_policy at all', async () => {
    getRequest.mockResolvedValue(providersResponse())
    await useAiProviderStore().loadForProject('flowgate')
    mountDialog()
    await flushPromises()
    await pickLoop()

    expect(optionValues('[data-test="review-loop-review-count"] option')).toEqual(['-1', '1', '2', '3'])
    const select = document.querySelector('[data-test="review-loop-review-count"]') as HTMLSelectElement
    expect(select.value).toBe('3')
  })
})
