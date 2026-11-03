/**
 * flowgate.v02.0125 T#2 — a `needs_intervention` card whose review attempts are used up
 * (`review_not_passed:review_count_exhausted`, Q #544) tells the user how it is released: one sentence in ko / en / ja,
 * shown by the Canvas card title and the Inspector pill tooltip alike. The menu `다음 단계` / `전체 실행` path stays the
 * only release: one `startRun` (step / full) and no other request (no stopRun, no resume), no extra button.
 *
 * This file: the sentence itself and the run-control contract (state only, no mounted Graph). What the user sees —
 * the mounted Designer Shell with the Canvas card, the Inspector pill, the top menu / toolbar and the same card
 * re-selected, after F5 and while the run moves — is cardInterventionShell.0125.spec.ts.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import type { FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import type { FlowGraphNodeRun } from '@/features/designer/center/graph/flowGraph'
import { fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { CARD_INTERVENTION_REASONS, cardStateReasonText, cardStateView } from '@/features/designer/center/graph/cardStateView'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { isRunLocked } from '@/features/designer/center/run/flowRunLock'
import { ALL_MENU_TARGETS, DESIGNER_MENUS, isMenuCommandEnabled, runPhaseOf } from '@/features/designer/top/designerTop'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { GRAPH_GRAPH, GRAPH_TEST_NODE, prepareInspectorI18n, selectionOf, targetOf } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
type Locale = 'ko' | 'en' | 'ja'
const MESSAGES = { ko, en, ja } as const
const LOCALES: readonly Locale[] = ['ko', 'en', 'ja']
const REASON = 'review_not_passed:review_count_exhausted'
const sentence = (locale: Locale): string => MESSAGES[locale].designer.center.card_intervention_reasons.review_count_exhausted

const exhaustedFacts = { status: 'review', intervention: { kind: 'needs_intervention', reason: REASON } } as const

describe('i18n sentence (ko / en / ja)', () => {
  it('maps the reason to one key, and every language has a sentence', () => {
    expect(CARD_INTERVENTION_REASONS[REASON]).toBe('review_count_exhausted')
    for (const locale of LOCALES) expect(sentence(locale).length).toBeGreaterThan(10)
  })

  it('ko names the four facts', () => {
    const text = sentence('ko')
    for (const part of ['검수 횟수', '마지막 수정본', '다음 단계', '전체 실행', '승인만으로는 완료되지 않습니다']) expect(text).toContain(part)
  })
})

describe('cardStateReasonText', () => {
  const t = (key: string) => `<${key}>`
  it('words the exhausted-review intervention', () => {
    expect(cardStateReasonText(cardStateView(exhaustedFacts), t)).toBe('<gui.designer.center.card_intervention_reasons.review_count_exhausted>')
    expect(cardStateReasonText(cardStateView({ status: 'review', awaitingApproval: true, approvalWaitReason: REASON }), t))
      .toBe('<gui.designer.center.card_intervention_reasons.review_count_exhausted>')
  })
  it('keeps every other reason as sent', () => {
    for (const reason of ['run_not_clean:timeout', 'issuer_lacks_approve', 'approval_refused', 'unknown', 'toString']) {
      expect(cardStateReasonText({ key: 'needs_intervention', reason }, t)).toBe(reason)
    }
    expect(cardStateReasonText({ key: 'incomplete', reason: REASON }, t)).toBe(REASON)
    expect(cardStateReasonText({ key: 'system_error', reason: REASON }, t)).toBe(REASON)
  })
})

describe('Canvas card and Inspector pill show the same sentence', () => {
  let card: VueWrapper<any> | null = null
  let header: VueWrapper<any> | null = null
  afterEach(() => {
    card?.unmount()
    header?.unmount()
    card = header = null
    i18n.global.locale.value = 'ko'
  })
  const cardTitle = () => {
    const data: FlowGateNodeData = { kind: 'plan', title: '카드', state: 'in_progress', selected: false, cardState: cardStateView(exhaustedFacts)! }
    card = mount(FlowNodeCard, { props: { id: 'gn_x', data }, global: { plugins: [i18n], stubs: { Handle: true } } })
    return card.get('[data-test="designer-canvas-node-card-state"]').attributes('title') ?? ''
  }
  const pillTitle = (reason: string) => {
    const nodeRun: FlowGraphNodeRun = { status: 'review', openedAt: null, finishedAt: null, resultDocId: 'result', intervention: { kind: 'needs_intervention', reason } }
    const node = { ...GRAPH_TEST_NODE, nodeRun }
    header?.unmount()
    header = mount(InspectorHeader, { props: { target: targetOf(selectionOf({ ...GRAPH_GRAPH, nodes: [node] }, node.id)) }, global: { plugins: [i18n] } })
    return header.get('[data-test="designer-inspector-pill"]').attributes('title') ?? ''
  }

  it.each(LOCALES)('%s: both carry the sentence, never the raw code', locale => {
    i18n.global.locale.value = locale
    const label = MESSAGES[locale].designer.center.card_states.needs_intervention
    const c = cardTitle()
    const p = pillTitle(REASON)
    expect(c).toBe(`${label} (${sentence(locale)})`)
    expect(p.endsWith(`${label} (${sentence(locale)})`)).toBe(true)
    expect(c).not.toContain('review_count_exhausted')
    expect(p).not.toContain('review_count_exhausted')
  })

  it('another reason stays raw in the pill; no re-review button on either surface', () => {
    i18n.global.locale.value = 'ko'
    expect(pillTitle('run_not_clean:timeout')).toContain('(run_not_clean:timeout)')
    cardTitle()
    expect(card!.findAll('button').length).toBe(0)
    expect(header!.html().toLowerCase()).not.toMatch(/재검수|retry|re-review/)
  })
})

describe('run control (state only): 다음 단계 / 전체 실행 on an idle run whose card is in needs_intervention (6-a / 6-d)', () => {
  const F = 'p.ui.0001'
  const NODE = 'gn_a'
  const NEXT = 'gn_b'
  // An idle run: the card's review attempts are used up and its last revision is not reviewed (Q #544).
  const exhaustedRun = (runId = 'run_0'): FlowRunInfo => ({
    runId,
    status: 'idle',
    openNodeKeys: [],
    nodeRuns: [{ nodeKey: NODE, status: 'review', intervention: { kind: 'needs_intervention', reason: REASON } }],
  } as unknown as FlowRunInfo)
  const nodeRunOf = (run: FlowRunInfo | null | undefined, key = NODE) => run?.nodeRuns?.find(nodeRun => nodeRun.nodeKey === key)
  const view = (run: FlowRunInfo | null | undefined, key = NODE) => {
    const nodeRun = nodeRunOf(run, key)
    return cardStateView({ status: nodeRun?.status ?? 'pending', intervention: nodeRun?.intervention ?? null })
  }

  interface Fake extends FlowGraphSource { calls: string[]; run: FlowRunInfo | null; hold: Promise<void> | null }
  function source(initial: FlowRunInfo | null = exhaustedRun()): Fake {
    const s = {
      ...fixtureFlowGraphSource,
      calls: [] as string[],
      run: initial,
      hold: null as Promise<void> | null,
      loadRun: async () => s.run,
      startRun: vi.fn(async (_p: string, flowId: string, mode?: string) => {
        s.calls.push(`start ${flowId} ${mode}`)
        await s.hold
        s.run = { runId: 'run_1', status: 'running', openNodeKeys: [NODE] }
        return s.run
      }),
      stopRun: vi.fn(async (_p: string, runId: string) => {
        s.calls.push(`stop ${runId}`)
        return s.run!
      }),
      resumeRun: vi.fn(async (_p: string, runId: string) => {
        s.calls.push(`resume ${runId}`)
        return s.run!
      }),
    }
    return s as unknown as Fake
  }
  const control = (s: FlowGraphSource) => {
    const runs = reactive<Record<string, FlowRunState>>({})
    const refresh = vi.fn()
    return { ctl: createFlowRunControl({ source: s, projectId: () => 'p', isOpen: () => true, prepare: () => true, refresh }, runs), runs, refresh }
  }
  /** What the top menu enables for a run status (DesignerShell routes run.next / run.all to center.runFlow). */
  const enabled = (status: string | null) =>
    (['run.next', 'run.all', 'run.pause', 'run.resume', 'run.stop', 'run.rerun', 'run.validate'] as const)
      .filter(cmd => isMenuCommandEnabled(cmd, runPhaseOf(status), { ...ALL_MENU_TARGETS, runBusy: false, runUnknown: false }))
  const runMenuCommands = (): string[] =>
    DESIGNER_MENUS.find(menu => menu.key === 'run')!.entries.flatMap(entry => (entry.kind === 'command' ? [entry.command as string] : []))

  it('the exhausted card is in the idle run; the menu is enabled and offers no separate re-review command', async () => {
    const s = source()
    const { ctl, runs } = control(s)
    ctl.read(F)
    await flushPromises()
    expect(runs[F].run?.status).toBe('idle')
    expect(isRunLocked(runs[F].run)).toBe(false)
    expect(nodeRunOf(runs[F].run)?.intervention).toEqual({ kind: 'needs_intervention', reason: REASON })
    expect(view(runs[F].run)?.key).toBe('needs_intervention')
    // The intervention does not disable 다음 단계 / 전체 실행, and the Run menu has no re-review entry.
    expect(enabled('idle')).toEqual(['run.next', 'run.all', 'run.validate'])
    expect(runMenuCommands()).toEqual(['run.next', 'run.all', 'run.stop', 'run.rerun', 'run.validate'])
  })

  it('전체 실행 on that run: one startRun(full) and nothing else; running locks; later commands are refused', async () => {
    const s = source()
    const { ctl, runs, refresh } = control(s)
    ctl.read(F)
    await flushPromises()
    let release!: () => void
    s.hold = new Promise<void>(resolve => (release = resolve))
    const going = ctl.run(F, 'all')
    await flushPromises()
    expect(await ctl.run(F, 'next')).toBe(false)   // while the first is answered
    release()
    expect(await going).toBe(true)
    expect(runs[F].run?.status).toBe('running')
    expect(isRunLocked(runs[F].run)).toBe(true)
    expect(enabled('running')).toEqual(['run.pause', 'run.stop'])
    // After the running answer a repeat is refused too, and still no request leaves.
    expect(await ctl.run(F, 'all')).toBe(false)
    expect(await ctl.run(F, 'next')).toBe(false)
    expect(s.startRun).toHaveBeenCalledTimes(1)
    expect(s.startRun).toHaveBeenCalledWith('p', F, 'full')
    expect(s.calls).toEqual([`start ${F} full`])
    expect(s.stopRun).not.toHaveBeenCalled()
    expect(s.resumeRun).not.toHaveBeenCalled()
    expect(refresh).toHaveBeenCalledWith(F)
  })

  it('다음 단계 on that run: one startRun(step); no stopRun and no other request', async () => {
    const s = source()
    const { ctl, runs } = control(s)
    ctl.read(F)
    await flushPromises()
    expect(await ctl.run(F, 'next')).toBe(true)
    expect(s.startRun).toHaveBeenCalledTimes(1)
    expect(s.startRun).toHaveBeenCalledWith('p', F, 'step')
    expect(s.stopRun).not.toHaveBeenCalled()
    expect(s.resumeRun).not.toHaveBeenCalled()
    expect(s.calls).toEqual([`start ${F} step`])
    expect(runs[F].run?.status).toBe('running')
    expect(await ctl.run(F, 'next')).toBe(false)
    expect(s.calls).toEqual([`start ${F} step`])
  })

  it('running -> done / completed: the card leaves intervention, no sentence (null reason), the menu is open again', async () => {
    const s = source()
    const { ctl, runs } = control(s)
    ctl.read(F)
    await flushPromises()
    expect(await ctl.run(F, 'all')).toBe(true)
    expect(runs[F].run?.status).toBe('running')
    s.run = { runId: 'run_1', status: 'idle', openNodeKeys: [], idleReason: 'completed', nodeRuns: [{ nodeKey: NODE, status: 'done', intervention: null }] } as unknown as FlowRunInfo
    ctl.read(F)
    await flushPromises()
    expect(runs[F].run?.status).toBe('idle')
    expect(isRunLocked(runs[F].run)).toBe(false)
    expect(view(runs[F].run)?.key).toBe('approved')
    // A done card has no reason at all: the helper answers null (its contract), never the exhausted sentence.
    expect(cardStateReasonText(view(runs[F].run), key => key)).toBeNull()
    expect(enabled('idle')).toEqual(['run.next', 'run.all', 'run.validate'])
  })

  it('다음 단계 settles the step: the reviewed card is done (no sentence), its successor waits as pending, the menu is open again', async () => {
    const s = source()
    const { ctl, runs } = control(s)
    ctl.read(F)
    await flushPromises()
    expect(await ctl.run(F, 'next')).toBe(true)
    expect(s.calls).toEqual([`start ${F} step`])
    // The step ended: the reviewed card is done, the card after it waits for the next command (pending propagation).
    s.run = {
      runId: 'run_1',
      status: 'idle',
      openNodeKeys: [],
      idleReason: 'step_done',
      pendingPropagationCount: 1,
      pendingNodeKeys: [NEXT],
      nodeRuns: [{ nodeKey: NODE, status: 'done', intervention: null }, { nodeKey: NEXT, status: 'pending', intervention: null }],
    } as unknown as FlowRunInfo
    ctl.read(F)
    await flushPromises()
    expect(runs[F].run?.status).toBe('idle')
    expect(runs[F].run?.pendingPropagationCount).toBe(1)
    expect(runs[F].run?.pendingNodeKeys).toEqual([NEXT])
    expect(nodeRunOf(runs[F].run)?.status).toBe('done')
    expect(nodeRunOf(runs[F].run, NEXT)?.status).toBe('pending')
    expect(view(runs[F].run)?.key).toBe('approved')
    expect(cardStateReasonText(view(runs[F].run), key => key)).toBeNull()
    expect(view(runs[F].run, NEXT)?.key).toBe('pending')
    expect(cardStateReasonText(view(runs[F].run, NEXT), key => key)).toBeNull()
    expect(isRunLocked(runs[F].run)).toBe(false)
    expect(enabled('idle')).toEqual(['run.next', 'run.all', 'run.validate'])
  })

  it('running -> idle with the card in needs_intervention again: the sentence is shown again, the menu is open again', async () => {
    const s = source()
    const { ctl, runs } = control(s)
    ctl.read(F)
    await flushPromises()
    expect(await ctl.run(F, 'all')).toBe(true)
    s.run = exhaustedRun('run_1')
    ctl.read(F)
    await flushPromises()
    expect(runs[F].run?.status).toBe('idle')
    expect(isRunLocked(runs[F].run)).toBe(false)
    expect(cardStateReasonText(view(runs[F].run), key => `<${key}>`)).toBe('<gui.designer.center.card_intervention_reasons.review_count_exhausted>')
    expect(enabled('idle')).toEqual(['run.next', 'run.all', 'run.validate'])
    // Another menu command is accepted again — still exactly one start per command.
    expect(await ctl.run(F, 'next')).toBe(true)
    expect(s.calls).toEqual([`start ${F} full`, `start ${F} step`])
  })
})
