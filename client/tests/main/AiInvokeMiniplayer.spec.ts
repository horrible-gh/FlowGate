// (A) 유지 — 0394 T0016 / NR0003 §6.3.
// 이 파일에서 소스를 읽는 것은 뒤쪽 세 케이스뿐이고, 대상은 SFC <style> 블록과
// shared/app.css의 선언값(위치·배경·테두리 알파)이다. jsdom은 스타일시트를 적용하지 않으므로
// 마운트해도 관찰할 수 없다 — 나머지 케이스는 전부 마운트해서 DOM을 본다.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import AiInvokeMiniplayer from '@main/components/AiInvokeMiniplayer.vue'
import { FINISHED_CARD_TTL_MS, useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { RETENTION_MIRROR_KEY } from '@shared/aiFinishedCardRetention'
import { useProjectStore } from '@main/stores/project'
import { useExplorerStore } from '@main/stores/explorer'
import { useToast } from '@main/components/common/useToast'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf-8')

function mountPlayer() {
  return mount(AiInvokeMiniplayer, { global: { plugins: [i18n] } })
}

// The monitor is a header chip whose cards live in a popover, so every card-level
// assertion has to open it first (0269 NR0011).
async function openPopover(wrapper: ReturnType<typeof mountPlayer>) {
  await wrapper.find('.aiv-mini__chip').trigger('click')
  await flushPromises()
}

describe('AiInvokeMiniplayer', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    useToast().toasts.value = []
    // bootstrap (active-all) + title/detail lookups
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return { data: { ok: true, runs: [], paused: [] } }
      if (url.includes('/documents/detail')) {
        return { data: { doc_id: 'x', title: '테스트 문서', type_code: 'R', file_path: 'a.md' } }
      }
      return { data: {} }
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  // 0269 재점검: with no run the monitor used to disappear entirely, so on the dashboard
  // there was nothing to see at all. It now stays as a muted chip in the header.
  it('stays visible as a muted chip while there is no run to monitor', async () => {
    const wrapper = mountPlayer()
    await flushPromises()
    const root = wrapper.find('.aiv-mini')
    expect(root.exists()).toBe(true)
    expect(root.classes()).toContain('aiv-mini--idle')

    const chip = wrapper.find('[data-test="ai-miniplayer-chip"]')
    expect(chip.exists()).toBe(true)
    expect(chip.attributes('title')).toBe(t('main.ai_miniplayer.idle_summary'))
    // Nothing to count while idle -> no badge.
    expect(wrapper.find('[data-test="ai-miniplayer-chip-badge"]').exists()).toBe(false)

    await openPopover(wrapper)
    expect(wrapper.find('.aiv-mini__empty').text()).toBe(t('main.ai_miniplayer.empty'))
    expect(wrapper.find('.aiv-mini__card').exists()).toBe(false)
    wrapper.unmount()
  })

  // CH0009 사용자 지시: "글자는 안넣어도 되니까" — the chip carries an icon and a count
  // only. Guard against a label creeping back in and widening the header.
  it('keeps the chip text-free and puts the summary in the tooltip', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-chip', group_id: 'flowgate.default.3010',
      doc_ref: 'flowgate.default.3010.0001-R', mode: 'continuous',
    })
    store.trackFinished({
      run_id: 'run-chip2', group_id: 'flowgate.default.3011', end_reason: 'user_paused',
    })
    await flushPromises()

    const chip = wrapper.find('[data-test="ai-miniplayer-chip"]')
    const summary = t('main.ai_miniplayer.fab_summary', { running: 1, waiting: 1 })
    expect(chip.attributes('title')).toBe(summary)
    expect(chip.attributes('aria-label')).toContain(summary)
    // The only text inside the chip is the numeric badge — no label.
    expect(chip.text().trim()).toBe('2')
    wrapper.unmount()
  })

  it('toggles the popover from the chip and closes on Escape', async () => {
    const wrapper = mountPlayer()
    await flushPromises()
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(false)

    await openPopover(wrapper)
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(true)
    expect(wrapper.find('.aiv-mini__chip').attributes('aria-expanded')).toBe('true')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(false)
    wrapper.unmount()
  })

  // The popover hides everything while closed, so an unanswered 질의 has to be visible
  // on the chip itself (NR0011 §5.2).
  it('badges the chip with the awaiting-answer count', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-qb', group_id: 'flowgate.default.3012',
      doc_ref: 'flowgate.default.3012.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3012.0005-Q')
    await flushPromises()

    const chip = wrapper.find('[data-test="ai-miniplayer-chip"]')
    expect(chip.classes()).toContain('aiv-mini__chip--awaiting')
    expect(wrapper.find('[data-test="ai-miniplayer-chip-badge"]').text()).toBe('1')
    wrapper.unmount()
  })

  it('drops the idle state as soon as a run arrives', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-idle', group_id: 'flowgate.default.3000',
      doc_ref: 'flowgate.default.3000.0001-R', mode: 'single',
    })
    await flushPromises()
    await openPopover(wrapper)
    expect(wrapper.find('.aiv-mini').classes()).not.toContain('aiv-mini--idle')
    expect(wrapper.find('.aiv-mini__empty').exists()).toBe(false)
    expect(wrapper.find('.aiv-mini__card').exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows the pause button only for continuous running cards', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-c', group_id: 'flowgate.default.3001',
      doc_ref: 'flowgate.default.3001.0001-R', mode: 'continuous', docs_target: 6,
    })
    await flushPromises()
    await openPopover(wrapper)
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.btn_pause'))

    // Control: a single-mode card must NOT offer pause (D0007 정지 흐름).
    store.trackStarted({
      run_id: 'run-s', group_id: 'flowgate.default.3002',
      doc_ref: 'flowgate.default.3002.0001-R', mode: 'single',
    })
    store.dismiss('flowgate.default.3001')
    delete store.runsByGroup['flowgate.default.3001']
    await flushPromises()
    expect(wrapper.text()).not.toContain(t('main.ai_miniplayer.btn_pause'))
    wrapper.unmount()
  })

  it('renders chain progress without regressing at a new hop', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0357'
    store.trackStarted({
      run_id: 'run-hop-1', group_id: groupId,
      doc_ref: `${groupId}.0001-B`, mode: 'continuous',
      docs_target: 5, chain_id: 'run-hop-1',
      chain_docs_target: 5, chain_docs_reached: 0,
    })
    store.trackStarted({
      run_id: 'run-hop-2', group_id: groupId, mode: 'continuous',
      docs_target: 4, chain_id: 'run-hop-1',
      chain_docs_target: 5, chain_docs_reached: 1,
    })
    await flushPromises()
    await openPopover(wrapper)

    expect(wrapper.find('.aiv-mini__progress-text').text()).toBe(
      t('main.ai_miniplayer.progress', { reached: 1, target: 5 }),
    )
    expect(wrapper.find('.aiv-mini__progress-fill').attributes('style')).toContain('width: 20%')
    wrapper.unmount()
  })

  it('renders a resume button and paused state for a paused chain', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-p', group_id: 'flowgate.default.3003',
      doc_ref: 'flowgate.default.3003.0001-R', mode: 'continuous', docs_target: 6,
    })
    store.trackFinished({
      run_id: 'run-p', group_id: 'flowgate.default.3003',
      end_reason: 'user_paused', docs_reached: 3, docs_target: 6,
    })
    await flushPromises()
    await openPopover(wrapper)
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.btn_resume'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.state_paused'))
    expect(wrapper.text()).not.toContain(t('common.close'))
    wrapper.unmount()
  })

  it('reports a restored resume failure instead of silently recreating the card', async () => {
    const groupId = 'flowgate.default.0383'
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-resume', group_id: groupId,
      doc_ref: `${groupId}.0001-B`, mode: 'continuous',
    })
    store.trackFinished({
      run_id: 'run-resume', group_id: groupId, end_reason: 'user_paused',
    })
    postRequest.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { code: 'resume_launch_failed', restored: true, resume_stage: 'advance_or_start' },
      },
    })
    await flushPromises()
    await openPopover(wrapper)

    const resume = wrapper.findAll('button').find(
      button => button.text().includes(t('main.ai_miniplayer.btn_resume')),
    )
    await resume!.trigger('click')
    await flushPromises()

    expect(useToast().toasts.value.at(-1)).toMatchObject({
      message: t('main.ai_miniplayer.error_resume_failed'),
      type: 'danger',
    })
    expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    wrapper.unmount()
  })

  // T0005 §3 item 4 / §4 item 4: a paused card whose explicit pin fell out of the
  // enabled chain must not offer the ordinary "정지됨 — 재개할 수 있습니다." copy or a
  // live [재개] button -- it names the blocker, the pin (when known), and points at
  // project AI settings, with the button disabled.
  it('shows a disabled resume button and blocker copy for an unavailable pinned provider', async () => {
    const groupId = 'flowgate.default.0456'
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) {
        return {
          data: {
            ok: true,
            runs: [],
            paused: [{
              group_id: groupId,
              doc_ref: `${groupId}.0001-B`,
              paused_at: '2026-08-24T12:00:00+09:00',
              resume_available: false,
              resume_block_code: 'provider_unavailable',
              resume_block_reason: 'The selected AI provider is not enabled for this project.',
              resume_provider_name: 'Old CLI',
            }],
          },
        }
      }
      return { data: {} }
    })
    const wrapper = mountPlayer()
    await useAiInvokeRunsStore().bootstrap()
    await flushPromises()
    await openPopover(wrapper)

    expect(wrapper.text()).toContain(t('main.ai_miniplayer.state_paused_unavailable'))
    expect(wrapper.text()).not.toContain(t('main.ai_miniplayer.state_paused'))
    const blocked = wrapper.find('[data-test="ai-miniplayer-resume-blocked"]')
    expect(blocked.exists()).toBe(true)
    expect(blocked.text()).toContain('The selected AI provider is not enabled for this project.')
    expect(blocked.text()).toContain('Old CLI')
    expect(blocked.text()).toContain(t('main.ai_miniplayer.resume_blocked_guidance'))

    const resumeBtn = wrapper.find('[data-test="ai-miniplayer-resume"]')
    expect(resumeBtn.exists()).toBe(true)
    expect(resumeBtn.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  // Stale-card variant: the card's cached resumeAvailable said "yes" (a settings
  // change raced it), so the button was live and got clicked -- the real 422 from
  // the server must show verbatim, not the generic error_resume_failed fallback,
  // and the paused card must not be dropped.
  it('shows the server 422 message verbatim for a stale resumable card and keeps it', async () => {
    const groupId = 'flowgate.default.0457'
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-stale', group_id: groupId,
      doc_ref: `${groupId}.0001-B`, mode: 'continuous',
    })
    store.trackFinished({
      run_id: 'run-stale', group_id: groupId, end_reason: 'user_paused',
    })
    postRequest.mockRejectedValueOnce({
      response: {
        status: 422,
        data: {
          code: 'provider_unavailable',
          message: 'The selected AI provider is not enabled for this project.',
        },
      },
    })
    await flushPromises()
    await openPopover(wrapper)

    const resumeBtn = wrapper.find('[data-test="ai-miniplayer-resume"]')
    expect(resumeBtn.attributes('disabled')).toBeUndefined()
    await resumeBtn.trigger('click')
    await flushPromises()

    expect(useToast().toasts.value.at(-1)).toMatchObject({
      message: 'The selected AI provider is not enabled for this project.',
      type: 'danger',
    })
    expect(useToast().toasts.value.at(-1)?.message).not.toBe(t('main.ai_miniplayer.error_resume_failed'))
    expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    wrapper.unmount()
  })

  it('shows the origin, code, run and timestamp of a system stop', async () => {
    const groupId = 'flowgate.default.0384'
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) {
        return {
          data: {
            ok: true,
            runs: [],
            paused: [{
              group_id: groupId,
              doc_ref: `${groupId}.0001-B`,
              paused_at: '2026-08-03T13:56:40+09:00',
              stop_kind: 'system',
              stop_code: 'no_output_exhausted',
              stop_run_id: 'aiv_old_chain',
            }],
          },
        }
      }
      return { data: {} }
    })
    const wrapper = mountPlayer()
    await useAiInvokeRunsStore().bootstrap()
    await flushPromises()
    await openPopover(wrapper)

    const details = wrapper.find('[data-test="ai-miniplayer-stop-details"]').text()
    expect(details).toContain(t('main.ai_miniplayer.stop_origin_system'))
    expect(details).toContain('no_output_exhausted')
    expect(details).toContain('aiv_old_chain')
    expect(details).toContain('2026-08-03T13:56:40+09:00')
    expect(wrapper.text()).toContain(`${groupId}.0001-B`)
    wrapper.unmount()
  })

  // 0393 B0001 / T0005 §2-6: "실패(작업 미반영)" with an empty error list was the entire
  // account the reporter got. The card now carries a sentence, not just a code.
  it('spells out why a run was stopped instead of leaving the card mute', async () => {
    const groupId = 'flowgate.default.0393'
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'aiv-0393', group_id: groupId, doc_ref: `${groupId}.0001-B`, mode: 'single',
    })
    store.trackFinished({
      run_id: 'aiv-0393', group_id: groupId, end_reason: 'exited', outcome: 'none',
      stop_code: 'group_lease_denied',
      stop_reason: "The group gate refused this run's own worker (GROUP_AI_RUN_OWNER_MISMATCH) on POST /flowgate/api/v1/inbox, so nothing it submitted was registered. A human must clear this: the run is not resumable.",
    })
    await flushPromises()
    await openPopover(wrapper)

    const reason = wrapper.find('[data-test="ai-miniplayer-stop-reason"]')
    expect(reason.exists()).toBe(true)
    // This build knows the code, so the reader gets it in their own language.
    expect(reason.text()).toBe(t('main.ai_miniplayer.stop_reason_group_lease_denied'))
    wrapper.unmount()
  })

  it('falls back to the server sentence for a stop code it has no wording for', async () => {
    const groupId = 'flowgate.default.0395'
    const sentence = '3 attempts on this hop ended without producing a document.'
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'aiv-0395', group_id: groupId, doc_ref: `${groupId}.0001-R`, mode: 'single',
    })
    store.trackFinished({
      run_id: 'aiv-0395', group_id: groupId, end_reason: 'exited', outcome: 'none',
      stop_code: 'no_output_exhausted', stop_reason: sentence,
    })
    await flushPromises()
    await openPopover(wrapper)

    expect(wrapper.find('[data-test="ai-miniplayer-stop-reason"]').text()).toBe(sentence)
    wrapper.unmount()
  })

  it('shows no stop line while the run is still going', async () => {
    const groupId = 'flowgate.default.0396'
    const wrapper = mountPlayer()
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv-0396', group_id: groupId, doc_ref: `${groupId}.0001-R`, mode: 'continuous',
    })
    await flushPromises()
    await openPopover(wrapper)

    expect(wrapper.find('[data-test="ai-miniplayer-stop-reason"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('highlights awaiting-answer cards with the Q badge', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-q', group_id: 'flowgate.default.3004',
      doc_ref: 'flowgate.default.3004.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3004.0005-Q')
    await flushPromises()
    await openPopover(wrapper)
    expect(wrapper.find('.aiv-mini__card--awaiting').exists()).toBe(true)
    expect(wrapper.text()).toContain(
      t('main.ai_miniplayer.awaiting_q_badge', { count: 1 }),
    )
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.awaiting_q_line'))
    wrapper.unmount()
  })

  it('opens the first pending Q from an awaiting card and closes the popover', async () => {
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return { data: { ok: true, runs: [], paused: [] } }
      if (url.includes('/documents/detail')) {
        const docId = decodeURIComponent(url.split('doc_id=')[1] ?? '')
        return {
          data: {
            doc_id: docId, title: 'Q 문서',
            type_code: docId.endsWith('-Q') ? 'Q' : 'R', file_path: 'q.md',
          },
        }
      }
      return { data: {} }
    })
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-q2', group_id: 'flowgate.default.3007',
      doc_ref: 'flowgate.default.3007.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3007.0006-Q')
    await flushPromises()
    await openPopover(wrapper)

    const openBtn = wrapper.findAll('button').find(
      b => b.text().includes(t('main.ai_miniplayer.btn_open_doc')),
    )
    expect(openBtn).toBeDefined()
    await openBtn!.trigger('click')
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('flowgate.default.3007.0006-Q'),
    )
    // Navigating away from the popover closes it — it must not linger over the document.
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(false)
    // ...and the run is still live, so opening its Q must NOT take the card away (0290).
    expect(store.runsByGroup['flowgate.default.3007']).toBeDefined()
    wrapper.unmount()
  })

  // 0290 R0001 §1: the card is the completion notice, so reading it (문서 열기) is what
  // retires it — not a stopwatch the user never sees.
  it('retires a finished card once its document has been opened', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-d', group_id: 'flowgate.default.3010',
      doc_ref: 'flowgate.default.3010.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-d', group_id: 'flowgate.default.3010',
      doc_ref: 'flowgate.default.3010.0001-R', outcome: 'complete',
    })
    await flushPromises()
    await openPopover(wrapper)

    const openBtn = wrapper.findAll('button').find(
      b => b.text().includes(t('main.ai_miniplayer.btn_open_doc')),
    )
    await openBtn!.trigger('click')
    await flushPromises()

    expect(store.runsByGroup['flowgate.default.3010']).toBeUndefined()
    wrapper.unmount()
  })

  // 0316 TR0005 rev1 반려 — "문서열기 해도 해당 프로젝트로 안가잖아": an AI run's document
  // very often belongs to a DIFFERENT project than the one on screen. Opening it used to
  // pass the *current* project id to the reveal, so the reveal landed on the wrong tree
  // and the explorer never moved to the document's project. The open must now switch the
  // active project to the doc's own project and select it there. Pinned end-to-end from
  // the header "문서 열기" the user actually clicks.
  it('switches to the document own project when opening a cross-project doc', async () => {
    const projectStore = useProjectStore()
    projectStore.setCurrentProject('proj-A')
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return { data: { ok: true, runs: [], paused: [] } }
      if (url.includes('/documents/detail')) {
        return {
          data: {
            doc_id: 'flowgate.default.9001.0001-R', title: '다른 프로젝트 문서',
            type_code: 'R', file_path: 'a.md', project_id: 'proj-B',
          },
        }
      }
      if (url.includes('/groups/tree')) {
        return {
          data: {
            data: {
              nodes: [
                {
                  id: 'flowgate.default.9001.0001-R', parent_id: 'grp-9001',
                  node_type: 'document', type_code: 'R', number: '0001', filename: null,
                  label: 'R0001', has_md: true, md_path: 'a.md',
                },
                {
                  id: 'grp-9001', parent_id: null, node_type: 'group', type_code: null,
                  number: '9001', filename: null, label: '9001', has_md: false, md_path: null,
                },
              ],
            },
          },
        }
      }
      return { data: {} }
    })
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-xp', group_id: 'flowgate.default.9001',
      doc_ref: 'flowgate.default.9001.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-xp', group_id: 'flowgate.default.9001',
      doc_ref: 'flowgate.default.9001.0001-R', outcome: 'complete',
    })
    await flushPromises()
    await openPopover(wrapper)

    const openBtn = wrapper.findAll('button').find(
      b => b.text().includes(t('main.ai_miniplayer.btn_open_doc')),
    )
    await openBtn!.trigger('click')
    await flushPromises()
    await flushPromises()

    const explorerStore = useExplorerStore()
    // The explorer is now on the document's project, not the one we started on...
    expect(projectStore.currentProjectId).toBe('proj-B')
    // ...and the opened doc is the selected node in that project's tree, with its
    // ancestor group expanded so it is actually revealed.
    expect(explorerStore.selectedGroupNodeId).toBe('flowgate.default.9001.0001-R')
    expect(explorerStore.isGroupNodeExpanded('proj-B', 'grp-9001')).toBe(true)
    wrapper.unmount()
  })

  it('offers a per-card remove and a bulk clear for finished cards only', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    for (const n of ['3011', '3012']) {
      store.trackStarted({
        run_id: `run-${n}`, group_id: `flowgate.default.${n}`,
        doc_ref: `flowgate.default.${n}.0001-R`, mode: 'single',
      })
      store.trackFinished({
        run_id: `run-${n}`, group_id: `flowgate.default.${n}`,
        doc_ref: `flowgate.default.${n}.0001-R`, outcome: 'complete',
      })
    }
    store.trackStarted({
      run_id: 'run-live', group_id: 'flowgate.default.3013',
      doc_ref: 'flowgate.default.3013.0001-R', mode: 'single',
    })
    await flushPromises()
    await openPopover(wrapper)

    // "닫기" was ambiguous next to the popover's own collapse control (0290 NR0003 §5.2).
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.btn_remove'))
    const removes = wrapper.findAll('[data-test="ai-miniplayer-remove"]')
    expect(removes).toHaveLength(2)
    await removes[0].trigger('click')
    expect(store.finishedCount).toBe(1)

    await wrapper.find('[data-test="ai-miniplayer-clear-finished"]').trigger('click')
    await flushPromises()
    expect(store.finishedCount).toBe(0)
    // The live run is untouched, and with nothing finished left the bulk action goes away.
    expect(store.runsByGroup['flowgate.default.3013']?.phase).toBe('running')
    expect(wrapper.find('[data-test="ai-miniplayer-clear-finished"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('orders awaiting and running cards above finished ones', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    // Ascending group id alone would invert this order.
    store.trackStarted({
      run_id: 'run-fin', group_id: 'flowgate.default.3020',
      doc_ref: 'flowgate.default.3020.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-fin', group_id: 'flowgate.default.3020',
      doc_ref: 'flowgate.default.3020.0001-R', outcome: 'complete',
    })
    store.trackStarted({
      run_id: 'run-live', group_id: 'flowgate.default.3021',
      doc_ref: 'flowgate.default.3021.0001-R', mode: 'single',
    })
    store.trackStarted({
      run_id: 'run-q', group_id: 'flowgate.default.3022',
      doc_ref: 'flowgate.default.3022.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3022.0002-Q')
    await flushPromises()
    await openPopover(wrapper)

    const docs = wrapper.findAll('.aiv-mini__doc').map(el => el.text())
    expect(docs).toEqual([
      'flowgate.default.3022.0001-R',
      'flowgate.default.3021.0001-R',
      'flowgate.default.3020.0001-R',
    ])
    wrapper.unmount()
  })

  // jsdom does not apply SFC <style> blocks nor render AppHeader's layout here, so these
  // two contracts are pinned at the source level instead.

  // The document full view dims the screen with the shared .modal-bg layer. The monitor
  // now lives in the header, so that dim has to start below the header or the chip is
  // unreachable while a document is being read (0269 D0002 / NR0011 §3).
  it('keeps the header reachable under the document full view overlay', () => {
    const mainPanel = read('../../src/main/components/MainPanel.vue')
    const appCss = read('../../shared/app.css')

    // The full view uses the below-header variant, not the bare full-screen dim.
    expect(mainPanel).toMatch(/class="modal-bg modal-bg--below-header"/)
    // ...and that variant actually starts at the header height.
    const variant = /\.modal-bg--below-header\s*\{([^}]*)\}/s.exec(appCss)?.[1] ?? ''
    expect(variant).toContain('top: var(--hdr-h)')
  })

  // The whole point of moving into the header: overlap is impossible by structure, so no
  // component measures another one's height any more (NR0011 §6).
  it('positions itself in the header instead of measuring bottom-fixed UI', () => {
    const sfc = read('../../src/main/components/AiInvokeMiniplayer.vue')
    const actionBar = read('../../src/main/components/ReviewActionBar.vue')

    const rootBlock = /\.aiv-mini\s*\{([^}]*)\}/s.exec(sfc)?.[1] ?? ''
    expect(rootBlock).toContain('position: relative')
    expect(rootBlock).not.toContain('position: fixed')
    // The action-bar height channel is gone on both ends: nobody reads the custom
    // property and nobody publishes it (the only remaining mention is a comment).
    expect(sfc).not.toContain('var(--fg-actionbar-h')
    expect(actionBar).not.toContain("setProperty('--fg-actionbar-h")
  })

  // T0017: the chip carries a quiet outline so it reads as a button before the badge
  // ever appears — but no fill, an outline fainter than the select's .14 beside it, and
  // still no divider line between the two (rev1 반려).
  it('outlines the chip faintly, with no fill and no divider against the provider selector', () => {
    const sfc = read('../../src/main/components/AiInvokeMiniplayer.vue')

    const rootBlock = /\.aiv-mini\s*\{([^}]*)\}/s.exec(sfc)?.[1] ?? ''
    expect(rootBlock).not.toMatch(/border(-right)?:\s*(?!none)/)

    const chipBlock = /\.aiv-mini__chip\s*\{([^}]*)\}/s.exec(sfc)?.[1] ?? ''
    const alpha = /border:\s*1px solid rgba\(255, 255, 255, \.(\d+)\)/.exec(chipBlock)?.[1]
    expect(alpha).toBeDefined()
    expect(Number(`.${alpha}`)).toBeLessThan(.14)
    expect(chipBlock).toContain('background: transparent')

    // The outline is constant: no state restates it, so hover/open/awaiting differ by
    // wash and colour only and the badge keeps the run signal to itself.
    const afterChipBlock = sfc.indexOf('}', sfc.indexOf('.aiv-mini__chip {')) + 1
    const chipStates = sfc.slice(afterChipBlock, sfc.indexOf('.aiv-mini__empty'))
    expect(chipStates).not.toContain('border-color')
    expect(chipStates).not.toMatch(/\bborder:/)
  })
})

// 0294 B0001 회귀: the finished card lives for FINISHED_CARD_TTL_MS, but the closed chip
// used to stop counting it the instant the run ended — and the popover is closed by
// default, so "완료" was the one state the user could never see. The store-level TTL test
// passed the whole time; only the chip's own signal was missing, so it is pinned here.
//
// 0452: FINISHED_CARD_TTL_MS is now the retention of somebody who has never opened the
// account screen, which is what this case is about — hence the cleared mirror below.
describe('AiInvokeMiniplayer — end-of-run signal on the closed chip', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    // Finished cards outlive the store now (per-tab persistence), so each case has to
    // start from an empty cache or it inherits the previous one's results.
    sessionStorage.clear()
    localStorage.removeItem(RETENTION_MIRROR_KEY)
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    getRequest.mockResolvedValue({ data: {} } as any)
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  const badge = (w: ReturnType<typeof mountPlayer>) =>
    w.find('[data-test="ai-miniplayer-chip-badge"]')

  it('holds the completion badge for the card TTL and drops it with the card', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-fin', group_id: 'flowgate.default.3020',
      doc_ref: 'flowgate.default.3020.0001-R', mode: 'single',
    })
    await nextTick()
    expect(badge(wrapper).text()).toBe('1')

    store.trackFinished({
      run_id: 'run-fin', group_id: 'flowgate.default.3020', outcome: 'complete', docs_reached: 1,
    })
    await nextTick()
    // The badge must NOT blink out with activeCount — this is the whole bug.
    expect(badge(wrapper).text()).toBe('1')
    // ...and it must not keep pretending the run is live either.
    expect(wrapper.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--done')
    expect(wrapper.find('.aiv-mini__chip').attributes('title')).toBe(
      t('main.ai_miniplayer.fab_summary_done', { running: 0, waiting: 0, done: 1 }),
    )

    vi.advanceTimersByTime(FINISHED_CARD_TTL_MS - 2_000)
    await nextTick()
    expect(badge(wrapper).exists()).toBe(true)
    expect(store.runsByGroup['flowgate.default.3020']).toBeDefined()

    // TTL reached: signal and card go together, never one before the other.
    vi.advanceTimersByTime(3_000)
    await nextTick()
    expect(badge(wrapper).exists()).toBe(false)
    expect(store.runsByGroup['flowgate.default.3020']).toBeUndefined()
    expect(wrapper.find('.aiv-mini').classes()).toContain('aiv-mini--idle')
    wrapper.unmount()
  })

  it('separates a clean finish from a partial one and from a lost run', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-partial', group_id: 'flowgate.default.3021', doc_ref: 'r' })
    store.trackFinished({
      run_id: 'run-partial', group_id: 'flowgate.default.3021', outcome: 'partial', docs_reached: 2,
    })
    await nextTick()
    expect(wrapper.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--alert')
    expect(badge(wrapper).text()).toBe('1')

    store.trackStarted({ run_id: 'run-lost', group_id: 'flowgate.default.3022', doc_ref: 'r' })
    store.markLost('flowgate.default.3022', 'run-lost')
    await nextTick()
    expect(wrapper.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--alert')
    expect(badge(wrapper).text()).toBe('2')
    wrapper.unmount()
  })

  it('counts running, paused and finished together, with 질의 대기 still winning', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-live', group_id: 'flowgate.default.3023', doc_ref: 'r', mode: 'continuous',
    })
    store.trackStarted({
      run_id: 'run-pz', group_id: 'flowgate.default.3024', doc_ref: 'r', mode: 'continuous',
    })
    store.trackFinished({ run_id: 'run-pz', group_id: 'flowgate.default.3024', end_reason: 'user_paused' })
    store.trackStarted({ run_id: 'run-done', group_id: 'flowgate.default.3025', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-done', group_id: 'flowgate.default.3025', outcome: 'complete' })
    await nextTick()
    // running + paused + finished-in-TTL — the finished one is no longer dropped.
    expect(badge(wrapper).text()).toBe('3')
    expect(wrapper.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--live')

    // An unanswered 질의 still outranks everything: it is the only state needing the user.
    store.trackQuestionRegistered('flowgate.default.3023.0005-Q')
    await nextTick()
    expect(badge(wrapper).text()).toBe('1')
    expect(wrapper.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--awaiting')
    wrapper.unmount()
  })

  // AppHeader remounts on every route change, so the popover state is gone — the finished
  // signal has to be rebuilt from the store alone or navigating loses the completion.
  it('rebuilds the finished signal after a remount', async () => {
    const first = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-nav', group_id: 'flowgate.default.3026', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-nav', group_id: 'flowgate.default.3026', outcome: 'complete' })
    await nextTick()
    first.unmount()

    const second = mountPlayer()
    await nextTick()
    expect(badge(second).text()).toBe('1')
    expect(second.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--done')

    // The popover still shows the finished card behind it (unchanged contract).
    await second.find('.aiv-mini__chip').trigger('click')
    await nextTick()
    expect(second.find('.aiv-mini__card--finished').exists()).toBe(true)
    expect(second.text()).toContain(t('main.ai_invoke_dialog.outcome_complete'))
    second.unmount()
  })

  // 0294 TR0005 rev1 반려: the retention contract the user is holding us to (0290) is a
  // 30-minute, reload-surviving card — not a 10-second blink. That work existed on its own
  // branch and never reached main, so the merged 0294 fix only mirrored the 10s window and
  // the completion still vanished "immediately". Pinned end-to-end on the chip so the two
  // halves can never drift apart again: minutes later, and after a page reload.
  it('still shows the completion minutes later and after a reload', async () => {
    const first = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-keep', group_id: 'flowgate.default.3027', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-keep', group_id: 'flowgate.default.3027', outcome: 'complete' })
    await nextTick()

    // Far past the old 10s window — the exact TTL is L0009/0290's call, but a user who
    // walked away for a few minutes must still find the result waiting.
    vi.advanceTimersByTime(5 * 60_000)
    await nextTick()
    expect(badge(first).text()).toBe('1')
    expect(first.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--done')

    // Reload: the registry is memory-only and /active-all never returns finished runs,
    // so only the per-tab cache can carry the result across (0290 NR0003 §3.5).
    first.unmount()
    store.$dispose()
    setActivePinia(createPinia())
    const reloaded = mountPlayer()
    await nextTick()
    expect(badge(reloaded).text()).toBe('1')
    expect(reloaded.find('.aiv-mini__chip').classes()).toContain('aiv-mini__chip--done')
    reloaded.unmount()
  })

  // 0401 NR0003 / T0004 작업 3: a lost card's server-side lease can outlive the process
  // that acquired it -- [제거] only ever cleared the CARD, never that lock, so the group
  // stayed stuck even with the card gone. [잠금 해제] must call the real release endpoint.
  describe('releasing a lost card lease', () => {
    function mountLostCard() {
      const wrapper = mountPlayer()
      const store = useAiInvokeRunsStore()
      store.trackStarted({ run_id: 'run-lost-1', group_id: 'flowgate.default.3030', doc_ref: 'r' })
      store.markLost('flowgate.default.3030', 'run-lost-1')
      return { wrapper, store }
    }

    it('shows [잠금 해제] on a lost card but not on a finished one', async () => {
      const { wrapper, store } = mountLostCard()
      store.trackStarted({ run_id: 'run-fin-1', group_id: 'flowgate.default.3031', doc_ref: 'r' })
      store.trackFinished({ run_id: 'run-fin-1', group_id: 'flowgate.default.3031', outcome: 'complete' })
      await flushPromises()
      await openPopover(wrapper)

      const releaseButtons = wrapper.findAll('[data-test="ai-miniplayer-release-lease"]')
      expect(releaseButtons).toHaveLength(1)
      expect(releaseButtons[0].text()).toContain(t('main.ai_miniplayer.btn_release_lease'))
      // [제거] must still be offered on BOTH cards -- releasing the lease is additive.
      expect(wrapper.findAll('[data-test="ai-miniplayer-remove"]')).toHaveLength(2)
      wrapper.unmount()
    })

    it('calls the release endpoint and drops the card on success', async () => {
      postRequest.mockResolvedValue({ data: { ok: true, released: true } })
      const { wrapper, store } = mountLostCard()
      await flushPromises()
      await openPopover(wrapper)

      await wrapper.find('[data-test="ai-miniplayer-release-lease"]').trigger('click')
      await flushPromises()

      expect(postRequest).toHaveBeenCalledWith(
        '/api/v1/ai-invoke/leases/flowgate.default.3030/release', {},
      )
      expect(store.runsByGroup['flowgate.default.3030']).toBeUndefined()
      wrapper.unmount()
    })

    it('shows the still-live reason inline on a 409 and keeps the card', async () => {
      postRequest.mockRejectedValue({ response: { status: 409, data: { code: 'run_still_live' } } })
      const { wrapper, store } = mountLostCard()
      await flushPromises()
      await openPopover(wrapper)

      await wrapper.find('[data-test="ai-miniplayer-release-lease"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-test="ai-miniplayer-release-error"]').text())
        .toBe(t('main.ai_miniplayer.error_release_lease_still_live'))
      expect(store.runsByGroup['flowgate.default.3030']).toBeDefined()
      wrapper.unmount()
    })

    it('treats a 404 (already gone) as success and drops the card', async () => {
      postRequest.mockRejectedValue({ response: { status: 404 } })
      const { wrapper, store } = mountLostCard()
      await flushPromises()
      await openPopover(wrapper)

      await wrapper.find('[data-test="ai-miniplayer-release-lease"]').trigger('click')
      await flushPromises()

      expect(store.runsByGroup['flowgate.default.3030']).toBeUndefined()
      expect(wrapper.find('[data-test="ai-miniplayer-release-error"]').exists()).toBe(false)
      wrapper.unmount()
    })

    it('shows a generic failure message on an unexpected error', async () => {
      postRequest.mockRejectedValue(new Error('network down'))
      const { wrapper, store } = mountLostCard()
      await flushPromises()
      await openPopover(wrapper)

      await wrapper.find('[data-test="ai-miniplayer-release-lease"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-test="ai-miniplayer-release-error"]').text())
        .toBe(t('main.ai_miniplayer.error_release_lease_failed'))
      expect(store.runsByGroup['flowgate.default.3030']).toBeDefined()
      wrapper.unmount()
    })
  })
})
