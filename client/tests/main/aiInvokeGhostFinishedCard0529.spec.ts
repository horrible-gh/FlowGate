// flowgate.default.0529 B0001 — "제거 눌러도 안사라지잖아 언제까지 나오게 할건데".
//
// The reported card was NOT a paused one. At the time of the report
// `ai_invoke_paused_chains` held zero rows, so `isNonResumableSystemStop()` — and with it
// the whole `DELETE /paused/{group_id}` path 0500 T0004 built — could never reach it. The
// screenshot shows the header counting it in the "1 완료" band, next to
// [완료 항목 모두 지우기]: it was a FINISHED card, rebuilt on every bootstrap from
// `/ai-invoke/active-all`'s `runs` array, which restores durable document-review-loop rows
// with `persisted: true`.
//
// Two client-side links of that chain are pinned here:
//
//   1. `trackFinished` stamped `finishedAtMs = Date.now()` on a RESTORED row, so the
//      retention sweep's clock restarted on every bootstrap and a six-day-old card could
//      never expire. It now carries the run's own `finished_at`.
//   2. [목록에서 제거] was a local `dismiss()` for any finished card. For a restored one
//      that lasts exactly until the next bootstrap. It now goes through
//      `DELETE /ai-invoke/runs/{run_id}/card` and drops the card only on the server's
//      confirmation — the same rule `releasePaused` already follows.
//
// Both card surfaces are exercised, because they share one registry and one button: if
// only one of them asks the server, the ghost simply moves to the other screen.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeMiniplayer from '@main/components/AiInvokeMiniplayer.vue'
import AiRunMonitorCard from '@main/components/AiRunMonitorCard.vue'
import {
  isDurableFinishedCard,
  useAiInvokeRunsStore,
} from '@main/stores/aiInvokeRuns'
import { useToast } from '@main/components/common/useToast'

const { getRequest, postRequest, deleteRequest } = vi.hoisted(() => (
  { getRequest: vi.fn(), postRequest: vi.fn(), deleteRequest: vi.fn() }
))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
  patchRequest: vi.fn(),
  deleteRequest,
}))

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

// The reported card, field for field.
const RUN_ID = 'aiv_20260830_000075'
const GROUP = 'flowgate.default.0481'
const DOC_REF = 'flowgate.default.0481.0004-NR'
const FINISHED_AT = '2026-08-31T08:14:33+09:00'
const CARD_URL = `/api/v1/ai-invoke/runs/${encodeURIComponent(RUN_ID)}/card`

/** One restored review-loop row exactly as active-all ships it. */
function restoredRun(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    run_id: RUN_ID,
    group_id: GROUP,
    doc_ref: DOC_REF,
    status: 'finished',
    mode: 'single',
    outcome: 'none',
    end_reason: 'exited',
    stop_code: 'group_lease_denied',
    finished_at: FINISHED_AT,
    persisted: true,
    ...overrides,
  }
}

function activeAll(runs: Array<Record<string, unknown>>) {
  return { data: { ok: true, runs, paused: [] } }
}

describe('0529 B0001 — the finished card that would not go away (store)', () => {
  let store: ReturnType<typeof useAiInvokeRunsStore>

  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    deleteRequest.mockReset()
    getRequest.mockResolvedValue(activeAll([]) as any)
    store = useAiInvokeRunsStore()
  })

  async function bootstrapGhost(overrides: Record<string, unknown> = {}) {
    getRequest.mockResolvedValueOnce(activeAll([restoredRun(overrides)]) as any)
    await store.bootstrap()
  }

  it('marks a card active-all rebuilt from the database as persisted', async () => {
    await bootstrapGhost()

    expect(store.runsByGroup[GROUP]).toMatchObject({
      runId: RUN_ID, phase: 'finished', persisted: true,
    })
    expect(isDurableFinishedCard(store.runsByGroup[GROUP])).toBe(true)
  })

  it('a card this tab watched finish is not persisted and stays a local dismiss', async () => {
    store.trackStarted({ run_id: 'run-live', group_id: 'flowgate.default.0529', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-live', group_id: 'flowgate.default.0529', outcome: 'complete' })

    const entry = store.runsByGroup['flowgate.default.0529']
    expect(entry.persisted).toBe(false)
    expect(isDurableFinishedCard(entry)).toBe(false)

    await store.removeCard('flowgate.default.0529')

    expect(deleteRequest).not.toHaveBeenCalled()
    expect(store.runsByGroup['flowgate.default.0529']).toBeUndefined()
  })

  // Link 1: the restarted clock. Stamping Date.now() on every bootstrap is why a card
  // from 2026-08-31 was still on screen on 2026-09-06 with a 30-minute retention.
  it('carries the run own finish time, not the moment of the restore', async () => {
    await bootstrapGhost()

    expect(store.runsByGroup[GROUP].finishedAtMs).toBe(Date.parse(FINISHED_AT))
    expect(store.runsByGroup[GROUP].finishedAtMs).toBeLessThan(Date.now())
  })

  it('falls back to now when the restored row has no readable finish time', async () => {
    // A card with no finishedAtMs is not a finished card at all and would vanish from
    // the list, which is worse than keeping it one retention too long.
    await bootstrapGhost({ finished_at: null })

    expect(store.runsByGroup[GROUP].finishedAtMs).toBeGreaterThan(0)
    expect(store.runsByGroup[GROUP].phase).toBe('finished')
  })

  it('the retention sweep can finally reach the six-day-old restored card', async () => {
    await bootstrapGhost()
    expect(store.runsByGroup[GROUP]).toBeDefined()

    store.sweepFinishedCards()

    expect(store.runsByGroup[GROUP]).toBeUndefined()
  })

  // Link 2: the removal that did not stick.
  describe('removeCard on a restored finished card', () => {
    it('asks the server and drops the card on a confirmed dismissal', async () => {
      await bootstrapGhost()
      deleteRequest.mockResolvedValueOnce({
        data: { ok: true, run_id: RUN_ID, group_id: GROUP, dismissed: true, already_dismissed: false },
      } as any)

      await store.removeCard(GROUP)

      expect(deleteRequest).toHaveBeenCalledWith(CARD_URL)
      expect(store.runsByGroup[GROUP]).toBeUndefined()
    })

    it('treats already_dismissed as success too', async () => {
      await bootstrapGhost()
      deleteRequest.mockResolvedValueOnce({
        data: { ok: true, run_id: RUN_ID, dismissed: false, already_dismissed: true },
      } as any)

      await store.removeCard(GROUP)

      expect(store.runsByGroup[GROUP]).toBeUndefined()
    })

    it('keeps the card and rethrows when the server refuses', async () => {
      await bootstrapGhost()
      const rejection = { response: { status: 409, data: { code: 'run_still_active' } } }
      deleteRequest.mockRejectedValueOnce(rejection)

      await expect(store.removeCard(GROUP)).rejects.toBe(rejection)

      expect(store.runsByGroup[GROUP]?.phase).toBe('finished')
    })

    it('keeps the card on an unexpected 2xx that confirms nothing', async () => {
      await bootstrapGhost()
      deleteRequest.mockResolvedValueOnce({ data: { ok: true } } as any)

      await store.removeCard(GROUP)

      expect(store.runsByGroup[GROUP]?.phase).toBe('finished')
    })

    // The whole point: the server stops listing it, so the next bootstrap is silent.
    it('does not rehydrate the removed card on the next active-all', async () => {
      await bootstrapGhost()
      deleteRequest.mockResolvedValueOnce({
        data: { ok: true, dismissed: true, already_dismissed: false },
      } as any)
      await store.removeCard(GROUP)

      getRequest.mockResolvedValueOnce(activeAll([]) as any)
      await store.bootstrap()

      expect(store.runsByGroup[GROUP]).toBeUndefined()
    })
  })

  describe('dismissAllFinished', () => {
    it('sends the durable dismissal for a restored card', async () => {
      await bootstrapGhost()
      deleteRequest.mockResolvedValueOnce({
        data: { ok: true, dismissed: true, already_dismissed: false },
      } as any)

      await store.dismissAllFinished()

      expect(deleteRequest).toHaveBeenCalledWith(CARD_URL)
      expect(store.runsByGroup[GROUP]).toBeUndefined()
    })

    it('still clears purely local finished cards with no request at all', async () => {
      store.trackStarted({ run_id: 'run-local', group_id: 'flowgate.default.0529', doc_ref: 'r' })
      store.trackFinished({ run_id: 'run-local', group_id: 'flowgate.default.0529', outcome: 'complete' })

      await store.dismissAllFinished()

      expect(deleteRequest).not.toHaveBeenCalled()
      expect(store.runsByGroup['flowgate.default.0529']).toBeUndefined()
    })

    it('one refused card cannot strand the rest, and the refusal is still reported', async () => {
      await bootstrapGhost()
      store.trackStarted({ run_id: 'run-local', group_id: 'flowgate.default.0529', doc_ref: 'r' })
      store.trackFinished({ run_id: 'run-local', group_id: 'flowgate.default.0529', outcome: 'complete' })
      const rejection = { response: { status: 409, data: { code: 'run_still_active' } } }
      deleteRequest.mockRejectedValueOnce(rejection)

      // Silently swallowing this would leave the user staring at a card the button
      // claimed to have cleared -- the same shape of lie the whole bug is about.
      await expect(store.dismissAllFinished()).rejects.toBe(rejection)

      expect(store.runsByGroup['flowgate.default.0529']).toBeUndefined()
      expect(store.runsByGroup[GROUP]?.phase).toBe('finished')
    })
  })
})

describe('0529 B0001 — [목록에서 제거] on both card surfaces', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    deleteRequest.mockReset()
    useToast().toasts.value = []
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return activeAll([restoredRun()])
      return { data: {} }
    })
  })

  async function seed() {
    const store = useAiInvokeRunsStore()
    await store.bootstrap()
    return store
  }

  describe('miniplayer', () => {
    async function open() {
      const store = await seed()
      const wrapper = mount(AiInvokeMiniplayer, { global: { plugins: [i18n] } })
      await flushPromises()
      await wrapper.find('.aiv-mini__chip').trigger('click')
      await flushPromises()
      return { wrapper, store }
    }

    it('sends the durable dismissal without interrupting with a confirm', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
      deleteRequest.mockResolvedValue({
        data: { ok: true, dismissed: true, already_dismissed: false },
      })
      const { wrapper, store } = await open()

      await wrapper.find('[data-test="ai-miniplayer-remove"]').trigger('click')
      await flushPromises()

      expect(confirmSpy).not.toHaveBeenCalled()
      expect(deleteRequest).toHaveBeenCalledWith(CARD_URL)
      expect(store.runsByGroup[GROUP]).toBeUndefined()
      wrapper.unmount()
    })

    it('keeps the card and says why when the server refuses', async () => {
      deleteRequest.mockRejectedValue({ response: { status: 409, data: { code: 'run_still_active' } } })
      const { wrapper, store } = await open()

      await wrapper.find('[data-test="ai-miniplayer-remove"]').trigger('click')
      await flushPromises()

      expect(store.runsByGroup[GROUP]?.phase).toBe('finished')
      expect(useToast().toasts.value.at(-1)).toMatchObject({
        message: t('main.ai_miniplayer.error_remove_card_still_active'),
        type: 'danger',
      })
      wrapper.unmount()
    })

    it('reports a forbidden removal in its own words', async () => {
      deleteRequest.mockRejectedValue({ response: { status: 403, data: { code: 'run_card_forbidden' } } })
      const { wrapper } = await open()

      await wrapper.find('[data-test="ai-miniplayer-remove"]').trigger('click')
      await flushPromises()

      expect(useToast().toasts.value.at(-1)).toMatchObject({
        message: t('main.ai_miniplayer.error_remove_card_forbidden'),
        type: 'danger',
      })
      wrapper.unmount()
    })

    it('[완료 항목 모두 지우기] clears the durable card through the server too', async () => {
      deleteRequest.mockResolvedValue({
        data: { ok: true, dismissed: true, already_dismissed: false },
      })
      const { wrapper, store } = await open()

      await wrapper.find('[data-test="ai-miniplayer-clear-finished"]').trigger('click')
      await flushPromises()

      expect(deleteRequest).toHaveBeenCalledWith(CARD_URL)
      expect(store.runsByGroup[GROUP]).toBeUndefined()
      wrapper.unmount()
    })

    it('[완료 항목 모두 지우기] says why when the server refuses, instead of nothing', async () => {
      deleteRequest.mockRejectedValue({ response: { status: 403, data: { code: 'run_card_forbidden' } } })
      const { wrapper, store } = await open()

      await wrapper.find('[data-test="ai-miniplayer-clear-finished"]').trigger('click')
      await flushPromises()

      expect(store.runsByGroup[GROUP]?.phase).toBe('finished')
      expect(useToast().toasts.value.at(-1)).toMatchObject({
        message: t('main.ai_miniplayer.error_remove_card_forbidden'),
        type: 'danger',
      })
      wrapper.unmount()
    })
  })

  describe('dashboard card', () => {
    it('sends the same durable dismissal from the dashboard', async () => {
      deleteRequest.mockResolvedValue({
        data: { ok: true, dismissed: true, already_dismissed: false },
      })
      const store = await seed()
      const wrapper = mount(AiRunMonitorCard, { global: { plugins: [i18n] } })
      await flushPromises()

      await wrapper.find('[data-test="ai-run-monitor-remove"]').trigger('click')
      await flushPromises()

      expect(deleteRequest).toHaveBeenCalledWith(CARD_URL)
      expect(store.runsByGroup[GROUP]).toBeUndefined()
      wrapper.unmount()
    })

    it('keeps the card and says why when the server refuses', async () => {
      deleteRequest.mockRejectedValue({ response: { status: 500 } })
      const store = await seed()
      const wrapper = mount(AiRunMonitorCard, { global: { plugins: [i18n] } })
      await flushPromises()

      await wrapper.find('[data-test="ai-run-monitor-remove"]').trigger('click')
      await flushPromises()

      expect(store.runsByGroup[GROUP]?.phase).toBe('finished')
      expect(useToast().toasts.value.at(-1)).toMatchObject({
        message: t('main.ai_miniplayer.error_remove_card_failed'),
        type: 'danger',
      })
      wrapper.unmount()
    })
  })
})
