// 0566 T0007 — CH/WP body extraction out of MainPanel.vue into documents/.
//
// T#1 moved AC/DC/generic behind DocumentBodyRouter but left CH, WP and Q as named slots, so
// their markup (and therefore the decision "which body does this document get") still lived in
// MainPanel's template. T0007 moves those three too. Two of them carry state that a naive move
// would silently destroy, and that is what this file pins:
//
//   CH — the full view is a MOVE of one live ConversationView (Teleport), not a second mount.
//        An unsent draft, an in-flight AI call with its poll loop and spinner, and the inline
//        manual-copy panel all live in that instance. Re-mounting instead of teleporting would
//        still *look* right in a screenshot, so the assertions below are about identity.
//   WP — DocWorkflow's `sequence-updated` has to keep reaching the CURRENT WorkPlanEditor's
//        fetchPlan(). That seam was the 0434 fix for "the plan only updates after F5"; the
//        editor is no longer MainPanel's own template ref, so the relay has to carry it.
//
// The last case is the reason the extraction exists at all: a new special document type must
// be a new body component plus one branch in the router, never another branch in MainPanel.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { compileStyle, parse } from 'vue/compiler-sfc'
import i18n from '@shared/i18n'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  putRequest: (...a: unknown[]) => postRequest(...a),
  patchRequest: (...a: unknown[]) => postRequest(...a),
  deleteRequest: (...a: unknown[]) => postRequest(...a),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

import MainPanel from '@main/components/MainPanel.vue'
import ConversationView from '@main/components/ConversationView.vue'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import ConversationDocumentView from '@main/components/documents/ConversationDocumentView.vue'
import DocumentBodyRouter from '@main/components/documents/DocumentBodyRouter.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { mountMainPanel } from '../helpers/mountMainPanel'

const GROUP_ID = 'flowgate.default.0566'
const CH_TAB = {
  id: `${GROUP_ID}.0009-CH`,
  title: 'chat',
  path: 'documents/flowgate/main/default/0566/0009-CH_document.md',
  type: 'md',
  typeCode: 'CH',
  projectId: 'flowgate',
}
const WP_TAB = {
  id: `${GROUP_ID}.0004-WP`,
  title: 'plan',
  path: 'documents/flowgate/main/default/0566/0004-WP_document.md',
  type: 'md' as const,
  typeCode: 'WP',
  projectId: 'flowgate',
}

// MainPanel resolves the group and the project off DocHeader's exposures; a bare shallow stub
// exposes nothing, which would leave the run layer and the mention path inert and let the
// assertions below pass for the wrong reason.
const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  setup(_props, { expose }) {
    expose({ groupId: ref(GROUP_ID), canEditDocument: ref(true), docProjectId: ref('flowgate') })
    return () => h('div', { class: 'doc-header-stub' })
  },
})

function seedTabs(tabs: Record<string, unknown>[] = [CH_TAB], activeTabId = tabs[0].id) {
  localStorage.setItem('flowgate.user.guest.tabs', JSON.stringify({ tabs, activeTabId }))
}

function mountPanel(stubs: Record<string, unknown> = {}) {
  return mount(MainPanel, {
    attachTo: document.body,
    shallow: true,
    global: {
      plugins: [i18n],
      stubs: {
        teleport: false,
        DocHeader: DocHeaderStub,
        DocumentBodyRouter: false,
        ConversationDocumentView: false,
        ConversationView: false,
        ...stubs,
      },
    },
  })
}

/** How many times a ConversationView has run its initial turn load — one per mount. */
function turnLoadCount(): number {
  return getRequest.mock.calls.filter(
    (c) => typeof c[0] === 'string' && (c[0] as string).includes('/conversation/turns'),
  ).length
}

/** The internal instance id. `.vm` hands back a fresh proxy on every access for a
 *  `<script setup>` component, so the object itself cannot be compared; the uid is stable
 *  for the life of one instance and changes the moment a second one is mounted. */
function chatUid(wrapper: { findComponent: (c: unknown) => { vm: unknown } }): number {
  const uid = (wrapper.findComponent(ConversationView as never).vm as { $?: { uid?: number } })?.$?.uid
  expect(typeof uid).toBe('number')
  return uid as number
}

/** The live composer, wherever it currently hangs — the card, or the full-view dialog it
 *  was teleported into (which lives outside the wrapper's own root element). */
function composerNode(): HTMLTextAreaElement {
  const node = document.body.querySelector('.conv-input')
  expect(node).not.toBeNull()
  return node as HTMLTextAreaElement
}

/** The chat AI run the in-flight case keeps alive across the round trip. */
const RUN_ID = 'run-chat-inflight'
/** pollRun waits 2500ms between polls; one step of this size releases exactly one poll. */
const POLL_STEP_MS = 2600
const MENTION = '@flowgate.default.0566.0009-CH read the conversation and append one AI turn'

/** Polls pollRun has actually issued for RUN_ID — the loop's only observable heartbeat. */
function pollCount(): number {
  return getRequest.mock.calls.filter((c) => c[0] === `/api/v1/ai-invoke/${RUN_ID}`).length
}

function startCount(): number {
  return postRequest.mock.calls.filter((c) => c[0] === '/api/v1/ai-invoke/start').length
}

/** A composer button by its label, found in the document rather than in the wrapper: while
 *  the full view is open the chat hangs under MainPanel's dialog, outside the card. */
function chatButton(label: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll('.conv-composer .conv-assist-btn')].find(
    (el) => (el.textContent ?? '').includes(label),
  )
  expect(button, `no chat button labelled "${label}"`).toBeTruthy()
  return button as HTMLButtonElement
}

/** The two things the user sees while a chat AI run is in flight (0264 R0001): the send
 *  button is a STOP control, and [Invoke AI] is a disabled spinner saying so. */
function expectRunInFlight(send: HTMLButtonElement): void {
  expect(document.body.querySelector('.conv-send'), 'the composer left the document').toBe(send)
  expect(send.classList.contains('is-stop'), 'send button is no longer the STOP control').toBe(true)
  expect(chatButton(i18n.global.t('main.conversation_view.invoke_ai_running')).disabled).toBe(true)
}

/** The insecure-origin reality this app is served under (B0001 / group 0240): the async write
 *  rejects and execCommand refuses, so a copy fails with its text already resolved — which is
 *  the only case that produces the inline manual-copy panel. */
function failEveryClipboardWrite(): void {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error('NotAllowedError')) },
  })
  ;(document as unknown as { execCommand: () => boolean }).execCommand = () => false
}

async function openChatFullView(wrapper: { find: (s: string) => { trigger: (e: string) => Promise<void> } }) {
  await wrapper.find('.conv-card .card-actions button').trigger('click')
  await flushPromises()
}

async function closeChatFullView() {
  ;(document.body.querySelector('.document-modal .modal-close') as HTMLElement).click()
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  localStorage.clear()
  getRequest.mockReset().mockResolvedValue({ data: {} })
  postRequest.mockReset().mockResolvedValue({ data: {} })
  seedTabs()
})

afterEach(() => {
  // The in-flight-run case leaves a pending poll timer; real timers drop it so it cannot
  // fire into the next case's mocks.
  vi.useRealTimers()
  delete (navigator as unknown as Record<string, unknown>).clipboard
  delete (document as unknown as Record<string, unknown>).execCommand
  document.body.innerHTML = ''
})

describe('CH body lives in documents/ConversationDocumentView (T0007 §3)', () => {
  it('routes the chat card through the body router instead of MainPanel template', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const router = wrapper.findComponent(DocumentBodyRouter)
    expect(router.exists()).toBe(true)
    const body = router.findComponent(ConversationDocumentView)
    expect(body.exists()).toBe(true)
    // The card markup travelled with the component: it is inside the new body, not a sibling
    // that MainPanel still renders around it.
    expect(body.find('.conv-card').exists()).toBe(true)
    expect(body.findComponent(ConversationView).exists()).toBe(true)
    expect(wrapper.findAllComponents(ConversationView)).toHaveLength(1)
  })

  it('keeps one and the same ConversationView instance across a full-view round trip', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const chat = wrapper.findComponent(ConversationView)
    const uid = chatUid(wrapper)
    const element = chat.element
    const loadsAfterMount = turnLoadCount()
    expect(loadsAfterMount).toBeGreaterThan(0)

    await wrapper.find('.conv-card .card-actions button').trigger('click')
    await flushPromises()

    // Identity, not resemblance: same instance id, same DOM node, only one of each, and no
    // second initial load — a re-mount would show up in all four.
    expect(wrapper.findAllComponents(ConversationView)).toHaveLength(1)
    expect(chatUid(wrapper)).toBe(uid)
    expect(wrapper.findComponent(ConversationView).element).toBe(element)
    expect(element.parentElement!.className).toContain('document-modal__body--conversation')
    expect(turnLoadCount()).toBe(loadsAfterMount)

    ;(document.body.querySelector('.document-modal .modal-close') as HTMLElement).click()
    await flushPromises()

    expect(wrapper.findAllComponents(ConversationView)).toHaveLength(1)
    expect(chatUid(wrapper)).toBe(uid)
    expect(wrapper.findComponent(ConversationView).element).toBe(element)
    expect(element.parentElement!.className).toContain('conv-card-bd')
    expect(turnLoadCount()).toBe(loadsAfterMount)
  })

  it('carries an unsent draft through the full view in the same composer node', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const composer = composerNode()
    composer.value = 'half-written question'
    composer.dispatchEvent(new Event('input'))
    await flushPromises()

    await wrapper.find('.conv-card .card-actions button').trigger('click')
    await flushPromises()

    // Same textarea element, still holding the text — the draft was moved, not restored into
    // a fresh one (a second mount could reload it from localStorage and look identical here,
    // which is why the node itself is asserted).
    expect(composerNode()).toBe(composer)
    expect(composerNode().value).toBe('half-written question')

    ;(document.body.querySelector('.document-modal .modal-close') as HTMLElement).click()
    await flushPromises()

    expect(composerNode()).toBe(composer)
    expect(composerNode().value).toBe('half-written question')
  })

  // T0007 §7-4 / §9-4. The state a re-mount destroys most visibly is a chat AI call in
  // flight: the run id, the poll loop watching it and the STOP/spinner presentation live
  // ONLY inside this ConversationView instance (0251 B0001 — a tab switch used to lose
  // them and the surface came back idle while the run kept going server-side). Nothing
  // above the chat holds that state, so nothing above it could restore it either.
  // Hence a real run here: [Invoke AI] → /ai-invoke/start → pollRun's repeated GETs, still
  // unfinished when the full view opens, while it is open, and after it closes.
  it('keeps an in-flight chat run polling, with its progress, across a full-view round trip', async () => {
    getRequest.mockImplementation(async (url: unknown) => {
      // [Invoke AI] is hidden outright when no provider is registered (D0005 §3-3), so the
      // run can only be started with a provider list in hand.
      if (url === '/api/v1/ai-invoke/providers') {
        return { data: { providers: [{ id: 'claude', name: 'Claude' }], default_provider_id: 'claude' } }
      }
      // The run never settles on its own: every poll answers 'running', so the loop is
      // genuinely in flight for the whole round trip instead of racing the assertions.
      if (url === `/api/v1/ai-invoke/${RUN_ID}`) return { data: { run_id: RUN_ID, status: 'running' } }
      return { data: {} }
    })
    postRequest.mockImplementation(async (url: unknown) =>
      url === '/api/v1/ai-invoke/start' ? { data: { ok: true, run_id: RUN_ID } } : { data: {} },
    )

    const wrapper = mountPanel()
    await flushPromises()

    // Only setTimeout is faked — flushPromises rides on setImmediate, and nothing else in
    // this tree should have its clock moved to step one poll loop.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })

    chatButton(i18n.global.t('main.conversation_view.invoke_ai')).click()
    await flushPromises()

    const uid = chatUid(wrapper)
    const loadsAfterMount = turnLoadCount()
    const send = document.body.querySelector('.conv-send') as HTMLButtonElement
    expectRunInFlight(send)
    expect(pollCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(POLL_STEP_MS)
    expect(pollCount()).toBe(1)

    await openChatFullView(wrapper)

    // Inside the full view: same instance, the same send-button node, still the STOP
    // control — and no second initial load, which a re-mount could not avoid.
    expect(chatUid(wrapper)).toBe(uid)
    expect(turnLoadCount()).toBe(loadsAfterMount)
    expectRunInFlight(send)
    // ...and the loop is still ticking in there, not merely painted as if it were.
    await vi.advanceTimersByTimeAsync(POLL_STEP_MS)
    expect(pollCount()).toBe(2)

    await closeChatFullView()

    expect(chatUid(wrapper)).toBe(uid)
    expect(turnLoadCount()).toBe(loadsAfterMount)
    expectRunInFlight(send)
    await vi.advanceTimersByTimeAsync(POLL_STEP_MS)
    expect(pollCount()).toBe(3)

    // One start for the whole trip: no leg re-issued or re-adopted the run.
    expect(startCount()).toBe(1)
    // Let the loop see `disposed` instead of leaving a timer behind for the next case.
    wrapper.unmount()
  })

  // T0007 §7-6 / §9-4. The inline manual-copy panel (B0001 / group 0240) is the CH-only
  // recovery for a mention copy that never reached the clipboard, and it crosses BOTH new
  // boundaries in BOTH directions: MainPanel owns the failed text, the router hands it down
  // as a prop, the chat renders the panel — and the [Close] click has to travel back up,
  // because only MainPanel can clear it. Drop the way down and a failed copy leaves the user
  // with no text at all; drop the way back and the panel cannot be dismissed.
  it('shows a failed mention copy in the chat panel and clears it on dismiss', async () => {
    failEveryClipboardWrite()
    postRequest.mockImplementation(async (url: unknown) =>
      url === '/api/v1/token/issue'
        ? { data: { token: 'tok', token_id: 'tid', mention: MENTION } }
        : { data: {} },
    )

    const wrapper = mountPanel()
    await flushPromises()
    const router = wrapper.findComponent(DocumentBodyRouter)
    expect(document.body.querySelector('.conv-manualcopy')).toBeNull()

    chatButton(i18n.global.t('main.conversation_view.copy_mention')).click()
    await flushPromises()

    // Down: the copy failed with the mention resolved, MainPanel recorded it, and it reached
    // the chat through the router with the text intact — recovery happens inside the chat.
    expect(router.props('conversationManualCopyText')).toBe(MENTION)
    const panel = wrapper.findComponent(ConversationDocumentView).find('.conv-manualcopy')
    expect(panel.exists()).toBe(true)
    expect((panel.find('.conv-manualcopy-text').element as HTMLTextAreaElement).value).toBe(MENTION)

    const close = [...panel.element.querySelectorAll('button')].find((el) =>
      (el.textContent ?? '').includes(i18n.global.t('common.close')),
    )
    expect(close, 'the manual-copy panel has no [Close]').toBeTruthy()
    ;(close as HTMLButtonElement).click()
    await flushPromises()

    // Up: manual-copy-dismiss → router → MainPanel.setConvManualCopy(tab, null). The prop
    // going back to null is the proof it was MainPanel that cleared it.
    expect(router.props('conversationManualCopyText')).toBeNull()
    expect(document.body.querySelector('.conv-manualcopy')).toBeNull()
  })

  it('relays the chat copy-mention through the router to MainPanel', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.conv-assist-btn').trigger('click')
    await flushPromises()

    // MainPanel.onConversationCopyMention is what mints a chat-scope token; seeing this call
    // proves the event crossed both new boundaries with the tab id still attached.
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/token/issue',
      expect.objectContaining({ action_scope: 'chat', doc_ref: CH_TAB.id }),
    )
  })

  it('keeps the chat read-only line separate from the shared document lock', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const runs = useAiInvokeRunsStore()
    const router = wrapper.findComponent(DocumentBodyRouter)

    // A run this chat started is its own: no document lock, and the composer stays usable so
    // ConversationView keeps its STOP-button path.
    runs.trackStarted({ run_id: 'run-own', group_id: GROUP_ID, doc_ref: CH_TAB.id, status: 'running' })
    await flushPromises()
    expect(router.props('readOnly')).toBe(false)
    expect(router.props('conversationReadOnly')).toBe(false)
    expect(wrapper.findComponent(ConversationView).props('readOnly')).toBe(false)

    // A run aimed at the next document locks the whole panel, and the chat with it. The two
    // lines travel as separate props because MainPanel — not the router — decides them.
    runs.dismiss(GROUP_ID)
    runs.trackStarted({
      run_id: 'run-next',
      group_id: GROUP_ID,
      doc_ref: `${GROUP_ID}.0010-TR`,
      status: 'running',
    })
    await flushPromises()
    expect(router.props('readOnly')).toBe(true)
    expect(router.props('conversationReadOnly')).toBe(true)
    expect(wrapper.findComponent(ConversationView).props('readOnly')).toBe(true)
  })

  it('still applies MainPanel conversation layout rule to the extracted card', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const card = wrapper.find('.conv-card').element
    const contentWrap = wrapper.find('.content-wrap--conversation').element
    const scopeId = [...contentWrap.attributes]
      .map((a) => a.name)
      .find((name) => name.startsWith('data-v-'))
    expect(scopeId, 'MainPanel should render with a scoped-style id').toBeDefined()

    // The card used to be MainPanel's own element, so `.content-wrap--conversation .conv-card`
    // matched it directly. Now it is a child component's root, and a plain descendant selector
    // in a `scoped` block would compile to `... .conv-card[data-v-MainPanel]` and quietly stop
    // matching — the chat card would lose its flex and stop filling the column. Compile the
    // real block with the real scope id and check the real element against what comes out.
    const source = readFileSync(join(process.cwd(), 'src/main/components/MainPanel.vue'), 'utf8')
    const style = parse(source, { filename: 'MainPanel.vue' }).descriptor.styles.find((s) => s.scoped)
    expect(style, 'MainPanel should keep a scoped style block').toBeTruthy()
    const compiled = compileStyle({
      source: style!.content,
      filename: 'MainPanel.vue',
      id: scopeId!,
      scoped: true,
    }).code.replace(/\/\*[\s\S]*?\*\//g, '')

    const convCardRules = [...compiled.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .map((m) => m[1].trim())
      .filter((selector) => /\.conv-card(?![\w-])/.test(selector))
    expect(convCardRules.length).toBeGreaterThan(0)
    for (const selector of convCardRules) {
      expect(card.matches(selector), `${selector} no longer matches the chat card`).toBe(true)
      // ...and it must reach the card explicitly, with `:deep()`. A plain descendant selector
      // happens to still match today only because Vue copies a parent's scope id onto a child
      // component's root element — and it does that solely while every component in between
      // renders a single root. The router is a v-if chain today; the day one branch gains a
      // sibling, the silent form breaks and the chat card loses its flex with nothing red.
      const target = selector.split(/\s+/).pop() ?? ''
      expect(target, `${selector} should use :deep() to cross into the body component`)
        .not.toContain(scopeId!)
    }
  })
})

describe('WP body is selected and wired by the body router (T0007 §4)', () => {
  const fetchPlan = vi.fn()
  const ensureSaved = vi.fn().mockResolvedValue('clean')

  const WorkPlanEditorStub = defineComponent({
    name: 'WorkPlanEditor',
    props: { docId: { type: String, default: '' }, readOnly: { type: Boolean, default: false } },
    setup(_props, { expose }) {
      expose({ fetchPlan, ensureSaved })
      return () => h('div', { class: 'wp-editor-stub' })
    },
  })

  const DocWorkflowStub = defineComponent({
    name: 'DocWorkflow',
    emits: ['sequence-updated'],
    setup(_props, { emit }) {
      return () => h('button', {
        class: 'doc-workflow-pour-stub',
        onClick: () => emit('sequence-updated'),
      })
    },
  })

  function mountWpPanel() {
    return mountMainPanel({
      tabs: [WP_TAB],
      stubs: { DocWorkflow: DocWorkflowStub, WorkPlanEditor: WorkPlanEditorStub },
    })
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    fetchPlan.mockClear()
    ensureSaved.mockClear()
  })

  it('renders the existing WorkPlanEditor from the router, with the document lock', async () => {
    const wrapper = await mountWpPanel()

    const router = wrapper.findComponent(DocumentBodyRouter)
    expect(router.exists()).toBe(true)
    const editor = router.findComponent(WorkPlanEditor)
    expect(editor.exists()).toBe(true)
    expect(editor.props('docId')).toBe(WP_TAB.id)
    expect(editor.props('readOnly')).toBe(false)
  })

  it('refreshes the router-owned editor when DocWorkflow reports a sequence change', async () => {
    const wrapper = await mountWpPanel()
    expect(fetchPlan).not.toHaveBeenCalled()

    await wrapper.find('.doc-workflow-pour-stub').trigger('click')

    // 0434: pouring a plan into the sequence must repaint the open WP tab without an F5. The
    // editor is no longer MainPanel's own template ref, so this only holds while the router
    // relays the instance back into workPlanEditorRefs.
    expect(fetchPlan).toHaveBeenCalledTimes(1)
  })

  it('keeps the pre-approval save on the same relayed editor instance', async () => {
    const wrapper = await mountWpPanel()

    const bar = wrapper.findComponent(ReviewActionBar)
    expect(bar.exists()).toBe(true)
    const beforeApprove = bar.props('beforeApprove') as () => Promise<boolean>
    expect(typeof beforeApprove).toBe('function')

    await expect(beforeApprove()).resolves.toBe(true)
    expect(ensureSaved).toHaveBeenCalledTimes(1)
  })
})

describe('MainPanel template owns no document body (T0007 §5-6)', () => {
  const source = readFileSync(join(process.cwd(), 'src/main/components/MainPanel.vue'), 'utf8')

  // The point of the extraction: adding T2/TR2 (or any other special document) must not mean
  // editing this template again. Each of these used to be mounted right here.
  it.each(['<ConversationView', '<WorkPlanEditor', '<QTDetailViewer'])(
    'no longer mounts %s itself',
    (tag) => {
      expect(source).not.toContain(tag)
    },
  )

  it('passes no body slot to the router', () => {
    expect(source).not.toMatch(/<template #(conversation|work-plan|question)>/)
    expect(source).not.toContain('</DocumentBodyRouter>')
  })
})
