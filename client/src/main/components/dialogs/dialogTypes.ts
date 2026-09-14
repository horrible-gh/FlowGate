/**
 * Common dialog layer — shared types and contract constants.
 *
 * flowgate.default.0560 T0012 (1순위 — 기반) / design: D0008 §2, logic: L0009 §1.
 *
 * This module is the single source of truth for every value L0009 §1 "파라미터 정의"
 * fixed: close reasons, action roles/tones, size/surface/variant, the DialogAction
 * data structure, the boolean-prop default table, the per-variant behaviour table and
 * the numeric parameters (footer ordering weights, z-order, scroll-lock init).
 *
 * Nothing here reaches into a feature: the common layer owns the frame and the
 * interaction contract only (D0008 §1).
 */
import type { InjectionKey } from 'vue'

/** L0009 §1 "공통 타입". Why a close *request* has a reason: DialogShell never flips
 *  `open` itself — it reports how the user asked to close and the feature decides
 *  (L0009 §1 불변식 4). `programmatic` is not user input and therefore is not filtered
 *  by the closeOnEscape/closeOnBackdrop options (L0009 §2 "Open / Close lifecycle"). */
export type DialogCloseReason =
  | 'header'
  | 'escape'
  | 'backdrop'
  | 'cancel'
  | 'programmatic'

/**
 * Semantic role of a footer action.
 *
 * `stop` (Type B 실행 중단) and `dismiss` (Type C transient dismiss) are deliberately
 * NOT folded into `cancel`/`aux`: D0008 §3 and NR0003 §7 forbid collapsing the three
 * cancel meanings into one class (L0009 §2 "Cancel / Stop / Dismiss").
 */
export type DialogActionRole = 'aux' | 'danger' | 'cancel' | 'primary' | 'stop' | 'dismiss'

export type DialogActionTone = 'default' | 'danger'

export type DialogSize = 'sm' | 'md' | 'lg' | 'xl'

export type DialogSurface = 'panel' | 'sheet'

/**
 * D0008 §2's nine target variants plus `alert`, which AlertDialog uses internally
 * (L0009 §1). Adding a variant is a D correction, not a change made here.
 */
export type DialogVariant =
  | 'confirm'
  | 'confirm-danger'
  | 'form'
  | 'form-actions'
  | 'workflow-large'
  | 'conflict-large'
  | 'progress'
  | 'blocking'
  | 'compact'
  | 'readonly'
  | 'alert'

/** Tone of an AlertDialog message (L0009 §2 "AlertDialog", D0008 §5). */
export type DialogAlertTone = 'info' | 'warning' | 'danger'

/**
 * Semantic footer action (L0009 §1 "semantic action 데이터 구조").
 *
 * Field-by-field constraints, kept here because the footer enforces them:
 *
 * - `id`      — stable identifier used both as the `action-{id}` slot name and as the
 *               running-action tracking key. It MUST be unique inside one `actions`
 *               array; a duplicate makes those two keys collide and is a contract
 *               violation (L0009 §4 "Contract violation 체크 분기").
 * - `label`   — button text.
 * - `role`    — ordering + semantic meaning; see `footerRolePriority`.
 * - `tone`    — visual tone only. `role='danger'` is a separate *position*; tone is
 *               what makes a primary button read as destructive.
 * - `disabled`/`loading` — owned by the CALLER. DialogFooter never writes these two
 *               fields (props objects are never mutated, L0009 §2 "Async action"); the
 *               in-flight state it owns lives in its internal `running_action_ids` and
 *               is OR-composed with these at render time.
 * - `order`   — relative order INSIDE the same role. It shares one number space with
 *               `caller_index` (the 0-based position in `actions`), and `0` is a valid
 *               value — presence is tested with `order !== undefined`, never with a
 *               falsy check (L0009 §2 "ordering 알고리즘"). It never overrides the role
 *               priority.
 * - `onSelect`— invoked by DialogFooter and by nothing else (L0009 §0 규칙 3). A
 *               returned Promise is awaited; its resolved value carries no meaning —
 *               whether the dialog closes on success is decided by the feature inside
 *               `onSelect`, and a rejection keeps the dialog open with an error shown.
 */
export interface DialogAction {
  id: string
  label: string
  role: DialogActionRole
  tone?: DialogActionTone
  disabled?: boolean
  loading?: boolean
  order?: number
  onSelect: (action: DialogAction) => void | Promise<void>
}

/** L0009 §3 "Dialog cleanup 상태". */
export type DialogCleanupState = 'active' | 'disposing' | 'cleaned'

/**
 * Close policy of a live dialog, as the centralised ESC/backdrop handler sees it.
 *
 * L0009 §2 "ESC" requires exactly ONE document keydown listener, owned by the stack
 * manager — so the decision tree in L0009 §4 has to read the active dialog's effective
 * policy from the stack entry rather than from the component that rendered it. This
 * object is that live view; DialogShell keeps it in sync with its own resolved props.
 */
export interface DialogEntryPolicy {
  variant: DialogVariant
  closeable: boolean
  closeOnEscape: boolean
  closeOnBackdrop: boolean
  busy: boolean
  blocking: boolean
}

/**
 * A dialog currently on the stack (L0009 §1 "dialog stack entry 데이터 구조").
 *
 * `policy` and `hooks` are the implementation seam for the two responsibilities the
 * stack manager owns but the component renders: the single ESC listener (see
 * DialogEntryPolicy) and the teardown effects `finalize_dialog_once` must run exactly
 * once (listener removal + `closed` emit live in DialogShell).
 */
export interface DialogStackEntry {
  /** instanceId. Also the seed for the generated ARIA ids. */
  id: string
  /** 0-based position in the stack; the z-order input. */
  stackIndex: number
  /** Dialog surface root element. */
  element: HTMLElement | null
  /** `document.activeElement` captured immediately before open. */
  triggerElement: HTMLElement | null
  /** Caller-nominated focus return point, or null. */
  explicitReturnTarget: HTMLElement | null
  closeable: boolean
  /** Parent entry (`stack.top()` at open time); null for a top-level dialog. */
  context: DialogStackEntry | null
  /** Set when this entry displays an imperative confirm()/alert() request. */
  request: DialogConfirmRequest | null
  /** True while this entry owns one unit of the body scroll-lock refcount. */
  scrollLockHeld: boolean
  cleanupState: DialogCleanupState
  policy: DialogEntryPolicy
  hooks: DialogEntryHooks
}

export interface DialogEntryHooks {
  /** Ask the owning DialogShell to emit `request-close(reason)`. */
  requestClose: (reason: DialogCloseReason) => void
  /** Detach focus trap / focusin listeners (finalize_dialog_once step). */
  removeFocusListeners: () => void
  /** Emit `closed` (finalize_dialog_once step). */
  emitClosed: () => void
}

/** Options accepted by the imperative `confirm()` / `alert()` APIs (L0009 §2). */
export interface DialogConfirmOptions {
  title: string
  message?: string
  danger?: boolean
  confirmLabel?: string
  cancelLabel?: string
  closeLabel?: string
  tone?: DialogAlertTone
}

export type DialogRequestKind = 'confirm' | 'alert'

export type DialogRequestState = 'PENDING' | 'RESOLVED_TRUE' | 'RESOLVED_FALSE'

/** L0009 §2 "request 데이터 구조" (ConfirmRequest). */
export interface DialogConfirmRequest {
  id: string
  kind: DialogRequestKind
  options: DialogConfirmOptions
  context: DialogStackEntry | null
  state: DialogRequestState
  /** Resolver of the Promise handed back to the caller. */
  resolve: (value: boolean) => void
  /** The stack entry displaying this request, or null while it is still queued. */
  entry: DialogStackEntry | null
}

/**
 * L0009 §1 "boolean props 기본값" — the single source of truth.
 *
 * Vue's type-only `defineProps` gives an omitted optional boolean the value `false`.
 * For the opt-OUT props below that silently inverts the contract, so every component
 * declares these defaults explicitly (`withDefaults`, or `?? variant default`) instead
 * of leaning on Vue's implicit `false`.
 *
 * `'variant'` means "take the value from `dialogVariantDefaults[variant]`"; an explicit
 * caller value always wins over the variant default.
 */
export const dialogBooleanDefaults = {
  /** opt-out — a dialog that cannot be closed must never be the default. */
  shellCloseable: true,
  /** opt-out — only progress/blocking turn this off, via the variant table. */
  shellCloseOnEscape: 'variant',
  /** opt-in — anything that can lose user input keeps this false. */
  shellCloseOnBackdrop: 'variant',
  /** opt-in. */
  shellBusy: false,
  /** opt-in — only the `blocking` variant is true. */
  shellBlocking: 'variant',
  /** opt-out — the X disappears only when a parent hands down `false`. */
  headerCloseable: true,
  /** opt-in. */
  footerBusy: false,
  /** opt-in. */
  footerDisabled: false,
  /** opt-in. */
  confirmDanger: false,
  /** opt-in. */
  actionDisabled: false,
  /** opt-in. */
  actionLoading: false,
} as const

export interface DialogVariantBehaviour {
  surface: DialogSurface
  closeOnEscape: boolean
  closeOnBackdrop: boolean
  blocking: boolean
  /** Default size for this variant — fixed by T0012 §4-2 (L0009 left it `[DEFERRED]`). */
  size: DialogSize
}

/**
 * L0009 §1 "variant별 동작 기본값" (surface / closeOnEscape / closeOnBackdrop /
 * blocking), plus the default `size` per variant that L0009 deferred to this T.
 *
 * `closeOnBackdrop=true` is given only to the two variants that cannot lose user input
 * by closing (compact / readonly).
 *
 * NOTE for the 3순위 이관 (NR0011 §1 결론 3, §9-1): 0412 T0004 removed backdrop-close
 * from every existing dialog. `QaHistoryDialog` / `QaReviewHistoryDialog` are slated to
 * move onto `readonly`, whose default below is `closeOnBackdrop=true` — i.e. the
 * default and the 0412 contract disagree. Nothing regresses today because no screen is
 * wired to this layer yet, but whoever performs that migration must decide explicitly
 * whether to keep the default or pass `:close-on-backdrop="false"`. This T does not
 * change the value: L0009 already fixed it.
 *
 * Size choices (T0012 §4-2), measured against the existing sizes in
 * `client/shared/app.css` so the new layer looks like the app it joins:
 *   sm = 400px  — `ConfirmModal.vue`'s `max-width:400px` confirm box
 *   md = 520px  — `.modal-box` (app.css L484), the default form/modal width
 *   lg = 720px  — `.modal-lg` (app.css L490)
 *   xl = min(1180px, 94vw) — `.document-modal` (app.css L492), the large workflow/
 *        conflict surface
 */
export const dialogVariantDefaults: Record<DialogVariant, DialogVariantBehaviour> = {
  'confirm': { surface: 'panel', closeOnEscape: true, closeOnBackdrop: false, blocking: false, size: 'sm' },
  'confirm-danger': { surface: 'panel', closeOnEscape: true, closeOnBackdrop: false, blocking: false, size: 'sm' },
  'form': { surface: 'panel', closeOnEscape: true, closeOnBackdrop: false, blocking: false, size: 'md' },
  'form-actions': { surface: 'panel', closeOnEscape: true, closeOnBackdrop: false, blocking: false, size: 'md' },
  'workflow-large': { surface: 'sheet', closeOnEscape: true, closeOnBackdrop: false, blocking: false, size: 'xl' },
  'conflict-large': { surface: 'sheet', closeOnEscape: true, closeOnBackdrop: false, blocking: false, size: 'xl' },
  'progress': { surface: 'panel', closeOnEscape: false, closeOnBackdrop: false, blocking: false, size: 'sm' },
  'blocking': { surface: 'panel', closeOnEscape: false, closeOnBackdrop: false, blocking: true, size: 'sm' },
  'compact': { surface: 'panel', closeOnEscape: true, closeOnBackdrop: true, blocking: false, size: 'sm' },
  'readonly': { surface: 'panel', closeOnEscape: true, closeOnBackdrop: true, blocking: false, size: 'lg' },
  'alert': { surface: 'panel', closeOnEscape: true, closeOnBackdrop: false, blocking: false, size: 'sm' },
}

/**
 * L0009 §1 "수치 파라미터" — footer ordering weights, ascending = left to right.
 *
 * DS0007's rule, expressed as numbers: `[보조/dismiss] [위험] [실행중단] [취소] [주버튼]`.
 * `cancel`(30) sits immediately left of `primary`(40), and a separate danger action
 * (20) sits left of cancel. `aux` and `dismiss` share weight 10 on purpose — their
 * relative order is settled by the 2nd/3rd sort keys, not by role.
 */
export const footerRolePriority: Record<DialogActionRole, number> = {
  aux: 10,
  dismiss: 10,
  danger: 20,
  stop: 25,
  cancel: 30,
  primary: 40,
}

/** L0009 §1 — at most one primary and one cancel per footer. */
export const footerRoleMaxCount: Partial<Record<DialogActionRole, number>> = {
  primary: 1,
  cancel: 1,
}

/**
 * z-order of the common dialog layer (L0009 §1 left `base`/`step` as `[DEFERRED]`;
 * T0012 §4-1 fixes them here).
 *
 * Measured z-index scale already in the tree (nothing below is modified by this T):
 *   200            app header band                      — app.css L35
 *   300/400/450/500 menus, dropdowns, miniplayer        — app.css L92/L735/L745/L1293,
 *                                                         GitActionMenu.vue L408,
 *                                                         AiInvokeMiniplayer.vue L813
 *   1000           `.modal-bg` / `.modal-overlay`       — app.css L478 / L827
 *   1000           ContextMenu.vue L87, ReviewActionBar.vue L1500
 *   1200           ReviewRejectDialog.vue L238, TimeMachineDialog.vue L386,
 *                  MainPanel.vue L5932
 *   1400           GitConflictResolverDialog.vue L638 — GONE as of 0560 T0024: that dialog is
 *                  a `conflict-large` member of this layer now and takes `base` + its stack
 *                  index like every other one. Left in the list because the figure below is
 *                  what `base` was chosen against, and nothing about that choice changed.
 *   1500           GitMergeReviewDialog.vue L945 (reject sub-dialog)
 *   2000           ToastContainer.vue L33, NotificationCenter.vue L667
 *
 * base = 1600 clears every legacy dialog (max 1500) without touching one of them, so a
 * common dialog opened while a legacy dialog is still on screen — the state this whole
 * priority-1 step leaves the app in, since no instance is migrated yet — lands on top.
 * It stays BELOW 2000 so toasts and the notification centre keep covering dialogs
 * exactly as they do today.
 *
 * step = 10 keeps nesting monotonically increasing with room for 39 nested levels
 * before reaching the toast layer; real nesting is 2 levels (the deepest case in the
 * whole NR0011 원장 is `GitMergeReviewDialog` + its reject sub-dialog).
 *
 * surface_offset = 1 (fixed by L0009 §1): the content panel renders one step above its
 * own overlay.
 */
export const dialogZOrder = {
  base: 1600,
  step: 10,
  surfaceOffset: 1,
} as const

/** L0009 §1 — `scroll_lock_refcount.init`. */
export const scrollLockRefcountInit = 0

/** Teleport host id; `document.body` is the fallback (L0009 §2 "Teleport"). */
export const dialogHostId = 'dialog-root'

/** Attribute an element inside the body carries to claim initial focus
 *  (`data_dialog_autofocus_element`, L0009 §4 "Initial focus 결정 트리"). */
export const dialogAutofocusAttribute = 'data-dialog-autofocus'

/** Attribute DialogFooter stamps on each rendered button so the initial-focus tree can
 *  find the primary / cancel button without knowing the feature's markup. */
export const dialogActionRoleAttribute = 'data-dialog-action-role'

/**
 * ARIA ids the shell generated, published to whatever renders the title/description
 * (normally DialogHeader). Provided rather than passed as props so the id in
 * `aria-labelledby` and the id on the title element can never disagree
 * (L0009 §2 "ARIA" 규칙 1).
 */
export interface DialogAriaContext {
  titleId: string
  descriptionId: string
}

export const dialogAriaKey: InjectionKey<DialogAriaContext> = Symbol('fg-dialog-aria')

/** ARIA id helpers (L0009 §2 "ARIA"). */
export function dialogTitleId(instanceId: string): string {
  return `dialog-${instanceId}-title`
}

export function dialogDescriptionId(instanceId: string): string {
  return `dialog-${instanceId}-description`
}
