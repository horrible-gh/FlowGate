/**
 * Common dialog layer — stack, lifecycle, scroll lock and imperative confirm/alert queue.
 *
 * flowgate.default.0560 T0012 §2.2 / logic: L0009 §2 "구현 책임 배치" ("small
 * helper/composable" 4항목), §2 "Open / Close lifecycle", §2 "Body scroll lock",
 * §2 "ConfirmDialog", §3 상태 전이.
 *
 * Everything here is module-level singleton state on purpose: L0009 §2 "Dialog stack"
 * defines ONE global stack whose last entry is the only active dialog, and §2 "ESC"
 * requires ONE document keydown listener owned by that stack manager. A per-component
 * copy of either would reintroduce exactly the "each screen decides for itself"
 * behaviour R0001 reported.
 *
 * The four rules of L0009 §0 that this file implements literally:
 *   1. teardown is one procedure — `disposeDialog`; `closeComplete` / `forceCleanup`
 *      are two entry points into it and carry no side effects of their own.
 *   2. idempotency comes from ownership flags (`cleanupState`, `scrollLockHeld`,
 *      `request.state`), never from counting calls.
 *   3. action execution has a single owner (DialogFooter — not this file).
 *   4. no "either/or" branches.
 */
import { shallowReactive } from 'vue'

import {
  dialogHostId,
  scrollLockRefcountInit,
  type DialogCloseReason,
  type DialogConfirmOptions,
  type DialogConfirmRequest,
  type DialogEntryHooks,
  type DialogEntryPolicy,
  type DialogStackEntry,
} from '../components/dialogs/dialogTypes'

/* ────────────────────────── dev warnings (L0009 §4) ────────────────────────── */

/**
 * Contract violations are development-time warnings only. Production must keep
 * rendering with a safe fallback — L0009 §4 "production fallback" — because a broken
 * action list is a bug in one screen, not a reason to take the app down.
 */
export function dialogDevWarn(message: string): void {
  if (import.meta.env.DEV) {
    console.warn(`[dialog] ${message}`)
  }
}

/* ────────────────────────────── dialog stack ───────────────────────────────── */

/**
 * `shallowReactive`, not `reactive`: entries are handed back to callers and compared by
 * identity all over this file (`stackTop() === entry`, `queue.active === request`). A
 * deep proxy would hand out a different object than the one it stores and every one of
 * those comparisons would silently become false.
 */
const stack = shallowReactive<DialogStackEntry[]>([])

let instanceSeq = 0

/** Stable per-dialog id; also seeds the generated ARIA ids (L0009 §2 "ARIA"). */
export function nextDialogInstanceId(): string {
  instanceSeq += 1
  return `fg${instanceSeq}`
}

/** L0009 §2 "Dialog stack" 규칙 1 — the last entry is the only active dialog. */
export function stackTop(): DialogStackEntry | null {
  return stack.length > 0 ? stack[stack.length - 1] : null
}

export function isTopDialog(entry: DialogStackEntry | null): boolean {
  return entry != null && stackTop() === entry
}

export function stackContains(entry: DialogStackEntry): boolean {
  return stack.indexOf(entry) !== -1
}

/** Entries opened after `entry` — topmost first, which is the teardown order. */
export function entriesAbove(entry: DialogStackEntry): DialogStackEntry[] {
  const index = stack.indexOf(entry)
  if (index === -1) return []
  return stack.slice(index + 1).reverse()
}

/** Read-only view for tests and for z-order/stack-index consumers. */
export function dialogStackEntries(): readonly DialogStackEntry[] {
  return stack
}

export interface RegisterDialogOptions {
  id: string
  element: HTMLElement | null
  explicitReturnTarget?: HTMLElement | null
  policy: DialogEntryPolicy
  hooks: DialogEntryHooks
}

/**
 * `open(dialog)` steps 1-6 of L0009 §2 "Open / Close lifecycle".
 *
 * Steps 7-11 (nextTick, ARIA wiring, initial focus, focus trap, `opened`) belong to
 * DialogShell — they need the rendered DOM.
 */
export function registerDialog(options: RegisterDialogOptions): DialogStackEntry {
  const active = document.activeElement
  const entry: DialogStackEntry = {
    id: options.id,
    stackIndex: stack.length,
    element: options.element,
    triggerElement: active instanceof HTMLElement ? active : null,
    explicitReturnTarget: options.explicitReturnTarget ?? null,
    closeable: options.policy.closeable,
    context: stackTop(),
    request: null,
    scrollLockHeld: false,
    cleanupState: 'active',
    policy: options.policy,
    hooks: options.hooks,
  }
  stack.push(entry)
  ensureKeydownListener()
  acquireScrollLock(entry)
  return entry
}

/**
 * Direct unregistration is an error path: the only legitimate caller is
 * `finalizeDialogOnce`, and it guards with `stackContains` first — so reaching here
 * with an entry that is not on the stack means someone bypassed teardown
 * (L0009 §4 "Contract violation 체크 분기", §5 경계 조건).
 */
export function unregisterDialog(entry: DialogStackEntry): void {
  const index = stack.indexOf(entry)
  if (index === -1) {
    dialogDevWarn(`unregister called for a dialog that is not on the stack (id=${entry.id})`)
    return
  }
  stack.splice(index, 1)
  for (let i = index; i < stack.length; i += 1) {
    stack[i].stackIndex = i
  }
  if (stack.length === 0) {
    teardownKeydownListener()
  }
}

/* ─────────────────────────── teardown (single path) ────────────────────────── */

/**
 * Normal close entry point — the feature set `open=false`.
 *
 * Teleport unmount is Vue's job here (the shell's `v-if` drops the subtree once the
 * feature flips `open`, and the host list drops an imperative request), so the
 * `unmount_teleport(dialog)` line of L0009 §2 has no imperative counterpart: it is the
 * reactive render that follows. The other two lines are below, in order.
 */
export function closeComplete(entry: DialogStackEntry): void {
  disposeDialog(entry)
  recomputeActiveDialog()
}

/** Forced entry point (owner component unmounting). Same procedure, no extra effects. */
export function forceCleanup(entry: DialogStackEntry): void {
  disposeDialog(entry)
  recomputeActiveDialog()
}

/**
 * The one teardown procedure (L0009 §2 "teardown — 단일 절차").
 *
 * Returns false when another entry point already took this dialog — that is the normal
 * no-op of a defensive second call, not a violation (L0009 §4 "정상 경로").
 */
export function disposeDialog(entry: DialogStackEntry): boolean {
  if (entry.cleanupState !== 'active') {
    return false
  }

  // ① Claim it first. From this moment `enqueue`/`showNext` refuse to attach a new
  //    child to this dialog, which is what closes the "flush → show_next → new child"
  //    race described in L0009 §2 "ConfirmDialog".
  entry.cleanupState = 'disposing'

  // ② Flush the confirm/alert queue this dialog is the context of — displayed AND
  //    still-queued requests, all to false, without show_next.
  disposeConfirmQueue(entry)

  // ③ Children, topmost first.
  for (const child of entriesAbove(entry)) {
    disposeDialog(child)
  }

  // ④ The single termination primitive.
  finalizeDialogOnce(entry)
  return true
}

/**
 * Every terminating side effect lives here and nowhere else, so "exactly once" is a
 * property of one guard instead of a rule each caller has to remember
 * (L0009 §0 규칙 2, §3 "Dialog cleanup 상태").
 */
export function finalizeDialogOnce(entry: DialogStackEntry): void {
  if (entry.cleanupState === 'cleaned') {
    return
  }
  entry.cleanupState = 'cleaned'

  if (entry.request != null) {
    // An imperative confirm/alert must never leave its Promise unresolved. If the user
    // already answered, resolveOnce is a no-op.
    resolveOnce(entry.request, false)
    removeDisplayedRequest(entry.request)
    releaseQueueOwnership(entry.request)
  }

  if (stackContains(entry)) {
    unregisterDialog(entry)
  }

  entry.hooks.removeFocusListeners()
  releaseScrollLock(entry)

  const target = resolveFocusReturnTarget(entry)
  if (target != null) {
    focusElement(target)
  }

  entry.hooks.emitClosed()
}

/**
 * Releases the owning queue's claim on `request` when finalizing *this* entry is what
 * ends it — i.e. the displayed confirm/alert's own stack entry was force-torn-down on
 * its own (host unmount of just that entry, HMR), not via `resolveRequest` and not via
 * `disposeConfirmQueue` acting on the request's context.
 *
 * `disposeConfirmQueue` already clears `queue.active` and deletes the queue entry
 * *before* the cascade reaches this entry's own `finalizeDialogOnce` (`disposeDialog`
 * order: queue flush → children → self), so in that path `queues.get(request.context)`
 * is already gone and this is a no-op. Without this, a lone forced teardown left
 * `queue.active` pointing at an already-resolved request forever, permanently blocking
 * `showNext`'s `queue.active != null` guard for that context (L0009 §2 "Queue").
 */
function releaseQueueOwnership(request: DialogConfirmRequest): void {
  const queue = queues.get(request.context)
  if (queue != null && queue.active === request) {
    queue.active = null
    showNext(request.context)
  }
}

/**
 * L0009 §2 "active dialog 재계산".
 *
 * `activate(top)` has no imperative body: every shell derives its own active state from
 * `isTopDialog(entry)`, so un-inerting and re-arming the focus trap happen by
 * reactivity the moment the stack changes. What does need doing is resuming the queues.
 */
export function recomputeActiveDialog(): void {
  const top = stackTop()
  if (top != null) {
    showNext(top)
  }
  showNext(null)
}

/* ──────────────────────────── ESC (single listener) ────────────────────────── */

let keydownListenerInstalled = false

function ensureKeydownListener(): void {
  if (keydownListenerInstalled) return
  document.addEventListener('keydown', onDocumentKeydown, true)
  keydownListenerInstalled = true
}

function teardownKeydownListener(): void {
  if (!keydownListenerInstalled) return
  document.removeEventListener('keydown', onDocumentKeydown, true)
  keydownListenerInstalled = false
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (handleEscape()) {
    event.stopPropagation()
    event.preventDefault()
  }
}

/**
 * L0009 §4 "ESC 허용 분기". The target is always `stackTop()`, which is why the
 * "parent handled it while a child was open" case cannot exist here by construction.
 *
 * Returns true when a close request was actually emitted.
 */
export function handleEscape(): boolean {
  const entry = stackTop()
  if (entry == null) return false

  const policy = entry.policy
  if (policy.blocking || policy.variant === 'blocking') return false
  if (!policy.closeable) return false
  if (policy.busy || policy.variant === 'progress') {
    // busy/progress do not forbid ESC on their own — the closeOnEscape option decides,
    // and the variant table already defaults progress/blocking to false.
    if (!policy.closeOnEscape) return false
    entry.hooks.requestClose('escape')
    return true
  }
  if (!policy.closeOnEscape) return false

  entry.hooks.requestClose('escape')
  return true
}

/**
 * L0009 §2 "Open / Close lifecycle" `request_close(dialog, reason)`.
 *
 * DialogShell calls this for every close request it sees; the shell emits
 * `request-close` only when this returns true. `programmatic` is not user input and is
 * therefore not filtered by the ESC/backdrop options.
 */
export function shouldEmitRequestClose(entry: DialogStackEntry, reason: DialogCloseReason): boolean {
  if (!isTopDialog(entry)) return false
  if (reason === 'programmatic') return true

  const policy = entry.policy
  if (policy.blocking) return false
  if (!policy.closeable) return false
  if (reason === 'escape' && !policy.closeOnEscape) return false
  if (reason === 'backdrop' && !policy.closeOnBackdrop) return false
  return true
}

/* ───────────────────────────────── focus ───────────────────────────────────── */

const FOCUSABLE_SELECTOR = [
  'button',
  'a[href]',
  'input',
  'select',
  'textarea',
  '[tabindex]',
  '[contenteditable="true"]',
].join(',')

/** L0009 §2 "Focus / Focusable" — candidate list minus the exclusion list. */
export function isFocusable(el: Element | null): el is HTMLElement {
  if (!(el instanceof HTMLElement)) return false
  if (!el.matches(FOCUSABLE_SELECTOR)) return false
  if (el.hasAttribute('disabled')) return false
  if (el.hasAttribute('hidden')) return false
  if (el.getAttribute('aria-hidden') === 'true') return false
  // `tabindex >= 0` is the candidate rule; a negative tabindex (the dialog container
  // itself, for one) is programmatically focusable but is not a Tab stop.
  const tabIndex = el.getAttribute('tabindex')
  if (tabIndex != null && Number(tabIndex) < 0) return false
  const style = el.ownerDocument.defaultView?.getComputedStyle(el)
  if (style && (style.display === 'none' || style.visibility === 'hidden')) return false
  return true
}

export function focusableElementsIn(root: HTMLElement | null): HTMLElement[] {
  if (root == null) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((el) => isFocusable(el))
}

export function firstFocusableIn(root: HTMLElement | null): HTMLElement | null {
  return focusableElementsIn(root)[0] ?? null
}

function isElementAlive(el: HTMLElement | null): boolean {
  return el != null && el.isConnected
}

/** The dialog entry whose surface contains `el`, or null when `el` is page content. */
function nearestDialogEntryContaining(el: HTMLElement): DialogStackEntry | null {
  for (let i = stack.length - 1; i >= 0; i -= 1) {
    const candidate = stack[i]
    if (candidate.element != null && candidate.element.contains(el)) {
      return candidate
    }
  }
  return null
}

function isInsideDisposingDialog(el: HTMLElement): boolean {
  const owner = nearestDialogEntryContaining(el)
  if (owner == null) return false
  return owner.cleanupState !== 'active'
}

function isFocusReturnCandidate(el: HTMLElement | null): el is HTMLElement {
  return isElementAlive(el) && isFocusable(el) && !isInsideDisposingDialog(el as HTMLElement)
}

/**
 * L0009 §2 "Focus return" — one function decides for both child close and top-level
 * close, so the two cannot drift apart.
 *
 * The cascade case (a parent is force-unmounted with children open) falls through 1 and
 * 2 because the child's trigger lives inside a `disposing` parent, and lands on the
 * main content root. Since `disposeDialog` walks child → parent, the LAST finalize to
 * run is the outermost dialog's, and its result is the final focus position.
 */
export function resolveFocusReturnTarget(entry: DialogStackEntry): HTMLElement | null {
  if (isFocusReturnCandidate(entry.triggerElement)) {
    return entry.triggerElement
  }
  if (entry.context != null && entry.context.cleanupState === 'active') {
    const first = firstFocusableIn(entry.context.element)
    if (first != null) return first
  }
  if (isFocusReturnCandidate(entry.explicitReturnTarget)) {
    return entry.explicitReturnTarget
  }
  return mainContentRoot()
}

/**
 * Fallback focus home. `null` when there is nothing sensible to focus — L0009 §5 is
 * explicit that focus is then left alone rather than thrown at `body`.
 */
function mainContentRoot(): HTMLElement | null {
  const root = document.querySelector<HTMLElement>('main') ?? document.getElementById('app')
  if (root == null) return null
  if (!root.hasAttribute('tabindex')) {
    // A content root is not focusable by default; -1 makes it programmatically
    // focusable without inserting it into the Tab order.
    root.setAttribute('tabindex', '-1')
  }
  return root
}

function focusElement(el: HTMLElement): void {
  try {
    el.focus()
  } catch {
    // jsdom and some browsers throw on detached/odd elements; focus is best-effort.
  }
}

/* ──────────────────────────── body scroll lock ─────────────────────────────── */

let scrollLockRefcount = scrollLockRefcountInit
let savedBodyOverflow: string | null = null

export function scrollLockCount(): number {
  return scrollLockRefcount
}

/**
 * L0009 §2 "Body scroll lock". The refcount is owned by per-entry occupancy
 * (`scrollLockHeld`), not by call counts — that is what makes "never below zero" a
 * consequence of the code rather than a promise in a comment.
 *
 * Every open entry counts, nested included, so closing a child does not unlock the body
 * while its parent is still open.
 */
export function acquireScrollLock(entry: DialogStackEntry): void {
  if (entry.scrollLockHeld) return
  entry.scrollLockHeld = true
  scrollLockRefcount += 1
  if (scrollLockRefcount === 1) {
    savedBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
}

export function releaseScrollLock(entry: DialogStackEntry): void {
  if (!entry.scrollLockHeld) return
  entry.scrollLockHeld = false
  scrollLockRefcount -= 1
  if (scrollLockRefcount < 0) {
    scrollLockRefcount = 0
    dialogDevWarn('scroll lock refcount underflow')
  }
  if (scrollLockRefcount === 0) {
    document.body.style.overflow = savedBodyOverflow ?? ''
    savedBodyOverflow = null
  }
}

/* ─────────────────── imperative confirm() / alert() queues ─────────────────── */

interface DialogConfirmQueue {
  active: DialogConfirmRequest | null
  pending: DialogConfirmRequest[]
}

/** One FIFO queue per context. The `null` key is the root (no dialog open) context. */
const queues = new Map<DialogStackEntry | null, DialogConfirmQueue>()

/**
 * Requests currently on screen. `ConfirmDialog`/`AlertDialog` in host mode render this
 * list; pushing to it is `display_as_toplevel` / `display_as_child` (which of the two
 * it becomes follows from the stack position the shell registers at), and removing from
 * it is the matching Teleport unmount.
 */
const displayedRequests = shallowReactive<DialogConfirmRequest[]>([])

export function displayedDialogRequests(): readonly DialogConfirmRequest[] {
  return displayedRequests
}

function removeDisplayedRequest(request: DialogConfirmRequest): void {
  const index = displayedRequests.indexOf(request)
  if (index !== -1) displayedRequests.splice(index, 1)
}

function queueFor(context: DialogStackEntry | null): DialogConfirmQueue {
  let queue = queues.get(context)
  if (queue == null) {
    queue = { active: null, pending: [] }
    queues.set(context, queue)
  }
  return queue
}

/**
 * L0009 §2 "Parent/nested 판정": the caller never names its parent. Whatever is active
 * at call time (before the first `await`) is the context, so the same call site works
 * both from page level and from inside a dialog.
 */
export function resolveConfirmContext(): DialogStackEntry | null {
  return stackTop()
}

let requestSeq = 0

function makeRequest(
  kind: DialogConfirmRequest['kind'],
  options: DialogConfirmOptions,
  resolve: (value: boolean) => void,
): DialogConfirmRequest {
  requestSeq += 1
  return {
    id: `req${requestSeq}`,
    kind,
    options,
    context: null,
    state: 'PENDING',
    resolve,
    entry: null,
  }
}

/**
 * Imperative confirm — the `window.confirm()` replacement (L0009 §2 "Imperative async
 * confirm"). Resolves exactly once: true for confirm, false for cancel / ESC / backdrop
 * / forced unmount / disposed context, which keeps the `if (!await confirm()) return`
 * control flow of the native call intact.
 */
export function confirm(options: DialogConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    enqueueRequest(makeRequest('confirm', options, resolve))
  })
}

/** Imperative alert (L0009 §2 "AlertDialog"). Always resolves `undefined`, never rejects. */
export function alert(options: DialogConfirmOptions): Promise<void> {
  return new Promise<void>((resolve) => {
    enqueueRequest(makeRequest('alert', options, () => resolve()))
  })
}

function enqueueRequest(request: DialogConfirmRequest): void {
  const context = resolveConfirmContext()
  if (context != null && context.cleanupState !== 'active') {
    // A dialog that is going away does not get new children; the caller's early return
    // still works because it gets an immediate false.
    resolveOnce(request, false)
    return
  }
  request.context = context
  queueFor(context).pending.push(request)
  showNext(context)
}

/**
 * L0009 §2 "Queue". Serial inside one context (native confirm's blocking feel), and
 * independent across contexts — a root confirm never waits behind a nested one.
 */
export function showNext(context: DialogStackEntry | null): void {
  if (context != null && context.cleanupState !== 'active') return
  if (context != null && stackTop() !== context) {
    // Not the active dialog right now; recomputeActiveDialog() retries when it is.
    return
  }
  const queue = queues.get(context)
  if (queue == null) return
  if (queue.active != null || queue.pending.length === 0) return
  queue.active = queue.pending.shift() ?? null
  if (queue.active != null) {
    displayedRequests.push(queue.active)
  }
}

/**
 * The only resolve primitive (L0009 §2 `resolve_once`). Returns true when this call is
 * the one that actually resolved; every later attempt is a silent no-op so that the
 * teardown paths can be blunt about re-resolving.
 */
export function resolveOnce(request: DialogConfirmRequest, value: boolean): boolean {
  if (request.state !== 'PENDING') return false
  request.state = value ? 'RESOLVED_TRUE' : 'RESOLVED_FALSE'
  // `alert` requests hold a resolver that discards the value — the true/false split is
  // internal queue bookkeeping and is never shown to an alert caller.
  request.resolve(value)
  return true
}

/** `on_resolve` — the normal path a displayed confirm/alert takes when the user answers. */
export function resolveRequest(request: DialogConfirmRequest, value: boolean): void {
  if (!resolveOnce(request, value)) {
    dialogDevWarn(`confirm request ${request.id} resolved twice from the UI; ignored`)
    return
  }
  closeRequestEntry(request)
  const queue = queues.get(request.context)
  if (queue != null && queue.active === request) {
    queue.active = null
    showNext(request.context)
  }
}

function closeRequestEntry(request: DialogConfirmRequest): void {
  if (request.entry != null && request.entry.cleanupState === 'active') {
    closeComplete(request.entry)
  }
  removeDisplayedRequest(request)
}

/**
 * Queue teardown shared by the normal and the forced path (L0009 §2
 * `dispose_confirm_queue`). Deliberately does NOT call `showNext`: the context is
 * already `disposing`, and reopening a child there is precisely the race this closes.
 */
export function disposeConfirmQueue(context: DialogStackEntry | null): void {
  const queue = queues.get(context)
  if (queue == null) return
  if (queue.active != null) {
    const active = queue.active
    queue.active = null
    resolveOnce(active, false)
    removeDisplayedRequest(active)
  }
  while (queue.pending.length > 0) {
    const pending = queue.pending.shift()
    if (pending != null) resolveOnce(pending, false)
  }
  // Drop the key so a dead entry is not kept alive by the queue map.
  queues.delete(context)
}

/**
 * Binds a displayed request to the stack entry that renders it.
 *
 * Two fields are set: `entry.request`, so a forced teardown of that entry alone still
 * resolves the Promise, and `entry.context`, which is restored to the request's own
 * context — a root-context request can legitimately be displayed while another
 * top-level dialog is open (L0009 §2: the root queue is always displayable), and in
 * that case the context the shell captured at registration is not the request's.
 */
export function linkRequestEntry(request: DialogConfirmRequest, entry: DialogStackEntry): void {
  if (request.entry === entry) return
  request.entry = entry
  entry.request = request
  entry.context = request.context
}

/* ──────────────────────────────── teleport host ────────────────────────────── */

/** L0009 §2 "Teleport" — one host for every dialog, `document.body` as the fallback. */
export function dialogTeleportTarget(): HTMLElement {
  return document.getElementById(dialogHostId) ?? document.body
}

/* ─────────────────────────────────── reset ─────────────────────────────────── */

/**
 * Drops all singleton state. Tests (and HMR) need this because the stack, the refcount
 * and the queues deliberately outlive any single component.
 */
export function resetDialogSystem(): void {
  stack.splice(0, stack.length)
  displayedRequests.splice(0, displayedRequests.length)
  queues.clear()
  scrollLockRefcount = scrollLockRefcountInit
  if (savedBodyOverflow != null) {
    document.body.style.overflow = savedBodyOverflow
    savedBodyOverflow = null
  }
  teardownKeydownListener()
}

/**
 * Aggregate accessor, so call sites can follow the `useXxx()` convention of
 * `client/src/main/composables/` (useFlowGateSse, useShortcuts, …) even though the state
 * itself is a singleton.
 */
export function useDialogStack() {
  return {
    stackTop,
    isTopDialog,
    stackContains,
    entriesAbove,
    dialogStackEntries,
    registerDialog,
    unregisterDialog,
    closeComplete,
    forceCleanup,
    disposeDialog,
    finalizeDialogOnce,
    recomputeActiveDialog,
    shouldEmitRequestClose,
    handleEscape,
    acquireScrollLock,
    releaseScrollLock,
    scrollLockCount,
    resolveFocusReturnTarget,
    focusableElementsIn,
    firstFocusableIn,
    isFocusable,
    confirm,
    alert,
    showNext,
    resolveRequest,
    resolveOnce,
    disposeConfirmQueue,
    linkRequestEntry,
    displayedDialogRequests,
    resolveConfirmContext,
    dialogTeleportTarget,
    nextDialogInstanceId,
    resetDialogSystem,
  }
}
