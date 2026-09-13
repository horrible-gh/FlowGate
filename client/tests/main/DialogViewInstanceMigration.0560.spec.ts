/**
 * flowgate.default.0560 T0018 (4순위) — the eleven View/component-internal modal·overlay
 * instances NR0005 §13 named, and what happened to each of them.
 *
 * Ten were moved onto `DialogShell`/`DialogHeader`/`DialogFooter`; the eleventh
 * (`src/main/views/ProjectsView.vue`) was deleted as dead code. Unlike 1~3순위, these were
 * never standalone `*Dialog.vue` files — the overlay markup sat inside a View or a feature
 * component, four of them in `MainPanel.vue` alone — so this spec has two jobs the 3순위 spec
 * did not: prove the old inline markup is really gone from its host file, and prove the
 * per-instance judgments §2.3 asked the TR to make are actually in the code.
 *
 * Where an instance already has a behavioural home, this spec does not duplicate it and says
 * where to look instead:
 *   - Document Full View's `convFullViewHost` teleport → `tests/components/MainPanelConversationFullView.spec.ts`
 *   - NotificationCenter's ESC / close / panel survival  → `tests/main/NotificationCenter.mockup0135.spec.ts`
 *   - WorkPlanAiScopeDialog's scope emission + footer order → `tests/main/WorkPlanAiScopeDialog.spec.ts`
 *   - WorkPlanEditor raw view                            → `tests/main/WorkPlanEditor.spec.ts`
 *   - the two non-Teleport overlays' before/after geometry → `tests/browser/dialog-local-overlay-geometry.0560.mjs`
 */
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
  putRequest: vi.fn().mockResolvedValue({ data: {} }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
  deleteRequest: vi.fn().mockResolvedValue({ data: {} }),
}))

vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import CommandsView from '../../src/settings/views/system/CommandsView.vue'
import MessagesView from '../../src/settings/views/project/MessagesView.vue'
import ConfirmDialog from '@main/components/dialogs/ConfirmDialog.vue'
import DocumentEditDialog from '@main/components/DocumentEditDialog.vue'
import DocumentFullViewDialog from '@main/components/DocumentFullViewDialog.vue'
import GitArchiveCatalogDialog from '@main/components/GitArchiveCatalogDialog.vue'
import QuickOpenDialog from '@main/components/QuickOpenDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import { useAuthStore } from '../../src/settings/stores/auth.js'

const CLIENT_DIR = resolve(__dirname, '../..')

function source(relative: string): string {
  return readFileSync(resolve(CLIENT_DIR, relative), 'utf8')
}

/* ─────────────────────── 1. the old markup is really gone ─────────────────────── */

/** The ten instances that moved, keyed by the file that hosts them (NR0011 원장 ID). */
const MIGRATED_FILES = [
  ['src/settings/views/project/MessagesView.vue', 51],
  ['src/settings/views/system/CommandsView.vue', 52],
  ['src/main/components/QuickOpenDialog.vue', 43],
  ['src/main/components/DocumentFullViewDialog.vue', 40],
  ['src/main/components/DocumentEditDialog.vue', 41],
  ['src/main/components/GitArchiveCatalogDialog.vue', 42],
  ['src/main/components/GitActionMenu.vue', 39],
  ['src/main/components/NotificationCenter.vue', 44],
  ['src/main/components/WorkPlanAiScopeDialog.vue', 32],
  ['src/main/components/WorkPlanEditor.vue', 45],
] as const

/**
 * Matched on class ATTRIBUTES, not on the word: several of these files still explain in prose
 * what the migration replaced, and a comment must be allowed to name `modal-bg` without
 * failing the check that no element carries it. `\b…\b` also keeps `document-modal__body`
 * (still a live class, the Teleport target of the chat full view) from reading as `modal-bd`.
 */
/**
 * The two instances 0560 T0020 (4.5순위) lifted out of their host file into a dialog component
 * of their own: host -> [NR0011 id, the new component].
 */
const EXTRACTED = new Map<string, [number, string]>([
  ['src/main/components/GitActionMenu.vue', [39, 'src/main/components/GitStatusPanelDialog.vue']],
  ['src/main/components/NotificationCenter.vue', [44, 'src/main/components/NotificationAiDetailDialog.vue']],
])

const LEGACY_CLASS = new RegExp(
  'class="[^"]*\\b('
  + 'modal-bg|modal-box|modal-hd|modal-bd|modal-ft|modal-close|modal-title|modal-overlay|modal'
  + '|wp-ai-scope|wp-ai-scope-card|wp-raw-overlay|wp-raw-box'
  + '|notif-dialog-backdrop|notif-dialog|git-panel-modal'
  + ')\\b',
)

describe('4순위 — the inline overlay markup is gone from its host file (T0018 §4-1)', () => {
  it.each(MIGRATED_FILES)('%s (NR0011 #%i) renders no legacy overlay element', (file) => {
    const text = source(file)
    expect(text.match(LEGACY_CLASS), `legacy overlay markup still in ${file}`).toBeNull()
    // ...and none of them opens a Teleport of its own any more: the common layer owns the host.
    expect(text, `${file} still teleports by hand`).not.toMatch(/<[Tt]eleport to="body">/)
  })

  /** Where the shell lives moved for two of these in 0560 T0020 (4.5순위) - see EXTRACTED. */
  it.each(MIGRATED_FILES.filter(([file]) => !EXTRACTED.has(file)))(
    '%s (NR0011 #%i) builds on the common shell',
    (file) => {
      const text = source(file)
      expect(text, `${file} is missing <DialogShell`).toContain('<DialogShell')
      expect(text, `${file} is missing <DialogHeader`).toContain('<DialogHeader')
    },
  )

  /**
   * 0560 T0020 (4.5순위) finished D0008 4's other half for these two: T0018 put them on the
   * common layer but left the shell block inside the host component. The host now holds open
   * state and data only, and the shell moved to a dialog component of its own.
   */
  it.each([...EXTRACTED.keys()])('%s hands its dialog to an extracted component', (file) => {
    const host = source(file)
    expect(host, `${file} still renders the shell itself`).not.toContain('<DialogShell')
    const child = EXTRACTED.get(file)![1]
    const tag = child.split('/').pop()!.replace('.vue', '')
    expect(host, `${file} no longer mounts its dialog`).toContain(`<${tag}`)
    const text = source(child)
    expect(text, `${child} is missing <DialogShell`).toContain('<DialogShell')
    expect(text, `${child} is missing <DialogHeader`).toContain('<DialogHeader')
  })

  /**
   * D0008 §4: "각 instance는 독립 dialog component로 분리하고, 부모 View는 open state와 데이터
   * 전달만 담당한다". MainPanel held four of them.
   */
  it('MainPanel hands its four instances to four components and keeps only their state', () => {
    const mainPanel = source('src/main/components/MainPanel.vue')
    for (const tag of [
      '<DocumentFullViewDialog',
      '<DocumentEditDialog',
      '<GitArchiveCatalogDialog',
      '<QuickOpenDialog',
    ]) {
      expect(mainPanel, `MainPanel no longer mounts ${tag}`).toContain(tag)
    }
    // None of the four bodies is left behind in the parent.
    expect(mainPanel.match(LEGACY_CLASS)).toBeNull()
    expect(mainPanel).not.toContain('<DialogShell')
    // The open flags and the data are still MainPanel's — that is the half it keeps.
    for (const state of ['fullViewVisible', 'editVisible', 'gitArchiveVisible', 'showQuickOpen']) {
      expect(mainPanel).toContain(`const ${state} = ref(`)
    }
  })
})

/* ───────────────────────── 2. the deletion (T0018 §2.3-1) ─────────────────────── */

describe('the dead main-side ProjectsView is deleted (T0018 §4-2)', () => {
  it('the file is gone', () => {
    expect(existsSync(resolve(CLIENT_DIR, 'src/main/views/ProjectsView.vue'))).toBe(false)
  })

  it('nothing imports it, and /projects still redirects to the living settings screen', () => {
    // The re-confirmation NR0011 §6 B-4 asked for, kept as a test so a future re-add is caught:
    // `/projects` renders an empty component and leaves before it ever mounts.
    const router = source('src/main/router/index.ts')
    expect(router).toContain("path: '/projects'")
    expect(router).toContain("window.location.href = `/settings/projects${query}`")
    expect(router).not.toContain('ProjectsView')

    // The living project create/edit screen is the settings one, and it is untouched by this T.
    expect(existsSync(resolve(CLIENT_DIR, 'src/settings/views/projects/ProjectsView.vue'))).toBe(true)
    expect(source('src/settings/router/index.js')).toContain("import ProjectsView from '../views/projects/ProjectsView.vue'")
  })
})

/* ──────────────── 3. closeOnBackdrop override (T0018 §2.2-3 / §4-4) ───────────── */

/**
 * The seven `compact`/`readonly` instances. L0009 §1 gives both variants
 * `closeOnBackdrop = true`, but 0412 T0004 fixed backdrop-no-close for every one of these and
 * NR0011's BD column records them all as `X`. Relying on the variant default would have
 * reverted that contract, so each one passes `false` explicitly.
 */
const BACKDROP_OVERRIDE_FILES = [
  ['src/main/components/WorkPlanAiScopeDialog.vue', 32],
  // 0560 T0020 moved 39 and 44 into components of their own; the override travelled with the
  // shell, so the contract is read off the file that renders it now.
  ['src/main/components/GitStatusPanelDialog.vue', 39],
  ['src/main/components/DocumentFullViewDialog.vue', 40],
  ['src/main/components/GitArchiveCatalogDialog.vue', 42],
  ['src/main/components/QuickOpenDialog.vue', 43],
  ['src/main/components/NotificationAiDetailDialog.vue', 44],
  ['src/main/components/WorkPlanEditor.vue', 45],
] as const

/** The three `form` instances, whose variant default is already `false`. */
const FORM_FILES = [
  'src/settings/views/project/MessagesView.vue',
  'src/settings/views/system/CommandsView.vue',
  'src/main/components/DocumentEditDialog.vue',
]

describe('backdrop-no-close survives the migration (T0018 §4-4)', () => {
  it.each(BACKDROP_OVERRIDE_FILES)('%s (NR0011 #%i) overrides close-on-backdrop explicitly', (file) => {
    expect(source(file), `${file} leans on the variant default`)
      .toMatch(/:close-on-backdrop="false"/)
  })

  it.each(FORM_FILES)('%s is a form variant, whose default is already false', (file) => {
    expect(source(file)).toMatch(/variant="form"/)
  })
})

/* ──────────────────────── 4. behaviour, instance by instance ─────────────────── */

const wrappers: VueWrapper[] = []

function track<T extends VueWrapper>(wrapper: T): T {
  wrappers.push(wrapper)
  return wrapper
}

function mountOptions(extra: Record<string, unknown> = {}) {
  return { global: { plugins: [i18n] }, attachTo: document.body, ...extra }
}

function surface(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('.fg-dialog-surface')
}

function overlay(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.fg-dialog-overlay')
  if (el == null) throw new Error('no dialog is open')
  return el
}

/** A real backdrop click: press AND release on the overlay itself (L0009 §4). */
function clickBackdrop(): void {
  const el = overlay()
  el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
}

function roles(): string[] {
  return [...document.body.querySelectorAll('[data-dialog-action-role]')]
    .map((el) => el.getAttribute('data-dialog-action-role') ?? '')
}

function actionIds(): string[] {
  return [...document.body.querySelectorAll('[data-dialog-action-id]')]
    .map((el) => el.getAttribute('data-dialog-action-id') ?? '')
}

function action(id: string): HTMLButtonElement {
  const button = document.body.querySelector<HTMLButtonElement>(`[data-dialog-action-id="${id}"]`)
  if (button == null) throw new Error(`footer action "${id}" is not rendered`)
  return button
}

function headerClose(): HTMLButtonElement {
  const button = document.body.querySelector<HTMLButtonElement>('.fg-dialog-header__close')
  if (button == null) throw new Error('the dialog header has no close button')
  return button
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  document.body.innerHTML = ''
})

afterEach(() => {
  while (wrappers.length > 0) {
    try {
      wrappers.pop()?.unmount()
    } catch {
      // already unmounted
    }
  }
  resetDialogSystem()
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

const TAB = {
  id: 'flowgate.default.0560.0018-T',
  title: '4순위 작업지시',
  path: 'documents/flowgate/main/default/0560/0018-T_document.md',
  type: 'md' as const,
  typeCode: 'T',
  projectId: 'flowgate',
}

/* — 40: Document Full View — */

describe('Document Full View (NR0011 #40, §2.3-2)', () => {
  function mountFullView(props: Record<string, unknown> = {}) {
    return track(mount(DocumentFullViewDialog, mountOptions({
      props: { visible: true, tab: TAB, icon: 'markdown-logo', canEdit: true, wrapLines: false, ...props },
      global: { plugins: [i18n], stubs: { MdViewer: true, TextViewer: true } },
    })))
  }

  it('opens below the header so the run monitor stays reachable (0269 D0002)', () => {
    mountFullView()
    expect(overlay().className).toContain('fg-dialog-overlay--below-header')
  })

  it('keeps the chat teleport target in its own element for a CH tab', () => {
    mountFullView({ tab: { ...TAB, typeCode: 'CH' } })
    // MainPanel's `CONV_FULL_VIEW_HOST` selector. The move itself is exercised end to end in
    // tests/components/MainPanelConversationFullView.spec.ts; this is the contract's other half.
    expect(document.body.querySelector('.document-modal__body--conversation')).not.toBeNull()
    expect(source('src/main/components/MainPanel.vue'))
      .toContain("const CONV_FULL_VIEW_HOST = '.document-modal__body--conversation'")
  })

  it('has no footer and stays open on a backdrop click', () => {
    const wrapper = mountFullView()
    expect(document.body.querySelector('.fg-dialog-footer')).toBeNull()
    clickBackdrop()
    expect(wrapper.emitted('close')).toBeFalsy()
    expect(surface()).not.toBeNull()
  })

  it('closes through the header X, and offers [수정] only when the tab is editable', async () => {
    const wrapper = mountFullView()
    const buttons = [...document.body.querySelectorAll('.fg-dialog-header__actions button')]
    expect(buttons.some((b) => b.textContent?.includes('수정'))).toBe(true)
    headerClose().click()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeTruthy()

    wrappers.pop()?.unmount()
    resetDialogSystem()
    document.body.innerHTML = ''
    mountFullView({ canEdit: false })
    const readOnly = [...document.body.querySelectorAll('.fg-dialog-header__actions button')]
    expect(readOnly.some((b) => b.textContent?.includes('수정'))).toBe(false)
  })
})

/* — 41: Document Edit — */

describe('Document Edit (NR0011 #41, §2.3-3)', () => {
  function mountConfirmHost() {
    return track(mount(ConfirmDialog as never, mountOptions({ props: { host: true } })))
  }

  function mountEdit(props: Record<string, unknown> = {}) {
    return track(mount(DocumentEditDialog, mountOptions({
      props: {
        visible: true,
        tab: TAB,
        body: '# loaded',
        fullContent: '',
        loadedBody: '# loaded',
        loadedFullContent: '',
        headerVisible: false,
        loading: false,
        saving: false,
        loadError: '',
        saveError: '',
        ...props,
      },
    })))
  }

  it('paints [취소] [저장] in the layer-owned order', () => {
    mountEdit()
    expect(roles()).toEqual(['cancel', 'primary'])
    expect(actionIds()).toEqual(['cancel', 'save'])
  })

  it('closes without asking while the editor is untouched', async () => {
    const wrapper = mountEdit()
    pressEscape()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  /**
   * The loss path this migration would otherwise have created. Before it, `@keydown.escape`
   * was in the markup but nothing inside the dialog held focus, so ESC only fired by accident
   * (NR0011 §9-5). On the common layer ESC is judged by the stack regardless of focus, so a
   * dirty editor has to be defended — §2.3-3 (a).
   */
  it.each([
    ['escape', () => pressEscape()],
    ['the header X', () => headerClose().click()],
    ['[취소]', () => action('cancel').click()],
  ])('asks before throwing away an unsaved edit closed by %s', async (_label, close) => {
    mountConfirmHost()
    const wrapper = mountEdit({ body: '# edited' })

    close()
    await flushPromises()

    // The confirm is up and the editor has NOT closed yet.
    expect(wrapper.emitted('close')).toBeFalsy()
    const confirmSurface = document.body.querySelector('[data-dialog-variant="confirm-danger"]')
    expect(confirmSurface, 'the discard confirm never appeared').not.toBeNull()

    // Answering "no" keeps the edit.
    confirmSurface!.querySelector<HTMLElement>('[data-dialog-action-role="cancel"]')!.click()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()

    // Answering "yes" lets it go.
    close()
    await flushPromises()
    document.body
      .querySelector<HTMLElement>('[data-dialog-variant="confirm-danger"] [data-dialog-action-role="primary"]')!
      .click()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('measures dirtiness against the textarea actually on screen', async () => {
    mountConfirmHost()
    // Header-edit mode: the body may differ from its baseline without the visible textarea
    // being dirty, and vice versa.
    const wrapper = mountEdit({
      headerVisible: true,
      body: '# edited',
      loadedBody: '# loaded',
      fullContent: '---\na: 1\n---\n# loaded',
      loadedFullContent: '---\na: 1\n---\n# loaded',
    })
    pressEscape()
    await flushPromises()
    expect(document.body.querySelector('[data-dialog-variant="confirm-danger"]')).toBeNull()
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('refuses every close path while a save is in flight', async () => {
    const wrapper = mountEdit({ saving: true, body: '# edited' })
    pressEscape()
    headerClose().click()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()
    expect(action('cancel').disabled).toBe(true)
  })
})

/* — 42: Git archive catalogue — */

const ARCHIVE_ITEM = {
  status: 'archived' as const,
  project_id: 'flowgate',
  group_id: 'flowgate.default.0559',
  title: 'earlier group',
  branch: 'flowgate_default_0559',
  archived_at: '2026-09-13T10:00:00+09:00',
  reason: null,
  base_sha: 'abcdef1234',
  head_sha: 'fedcba4321',
  head_ref: 'refs/fg-archive/0559/head',
  stash_sha: null,
  stash_ref: null,
  commit_count: 3,
  changed_file_count: 7,
}

describe('Git archive catalogue (NR0011 #42, §2.3-5)', () => {
  function mountCatalog(props: Record<string, unknown> = {}) {
    return track(mount(GitArchiveCatalogDialog, mountOptions({
      props: {
        visible: true,
        items: [ARCHIVE_ITEM],
        picked: [],
        purgeConfirmed: false,
        loading: false,
        busy: false,
        errorMessage: '',
        formatTime: (v: string | null) => v ?? '-',
        shortSha: (v: string | null) => (v ? v.slice(0, 7) : '-'),
        ...props,
      },
    })))
  }

  it('has a single dismiss action in the footer — no primary, no cancel', () => {
    mountCatalog()
    expect(roles()).toEqual(['dismiss'])
    expect(actionIds()).toEqual(['close'])
  })

  /**
   * §2.3-5, kept as a test rather than only as prose: `readonly` describes the FOOTER. The body
   * still restores and permanently deletes. A later reader who takes the variant name as a
   * safety statement is exactly what this case exists to contradict.
   */
  it('still carries its destructive actions in the body', () => {
    mountCatalog({ picked: [ARCHIVE_ITEM.group_id], purgeConfirmed: true })
    const body = document.body.querySelector('.fg-dialog-body')!
    expect(body.querySelector('.git-archive-row .btn-primary')).not.toBeNull()
    expect(body.querySelector('.git-archive-purge .btn-danger')).not.toBeNull()
    expect((body.querySelector('.git-archive-purge .btn-danger') as HTMLButtonElement).disabled)
      .toBe(false)
  })

  it('keeps the purge button gated on the acknowledgement and a selection', () => {
    mountCatalog()
    const purge = document.body.querySelector<HTMLButtonElement>('.git-archive-purge .btn-danger')!
    expect(purge.disabled).toBe(true)
  })

  it('stays open on a backdrop click', () => {
    const wrapper = mountCatalog()
    clickBackdrop()
    expect(wrapper.emitted('close')).toBeFalsy()
  })
})

/* — 43: Quick Open — */

describe('Quick Open (NR0011 #43, §2.3-4)', () => {
  function mountQuickOpen() {
    return track(mount(QuickOpenDialog, mountOptions({ props: { visible: true, query: '' } })))
  }

  /**
   * §2.3-4 is an instruction NOT to build anything: the input stays disabled and the body stays
   * a single notice. This case is the freeze — if someone implements search here, it fails and
   * they have to come back for a T that actually asks for it.
   */
  it('is still the disabled input and the notice, with no footer', () => {
    mountQuickOpen()
    const input = document.body.querySelector<HTMLInputElement>('.fg-dialog-body input')!
    expect(input.disabled).toBe(true)
    expect(document.body.querySelector('.fg-dialog-body .empty p')?.textContent?.trim())
      .toBe(i18n.global.t('main.main_panel.description_187'))
    expect(document.body.querySelector('.fg-dialog-footer')).toBeNull()
    // No result list grew in place of the notice.
    expect(document.body.querySelectorAll('.fg-dialog-body > *')).toHaveLength(2)
  })

  it('closes by the header X and not by the backdrop', async () => {
    const wrapper = mountQuickOpen()
    clickBackdrop()
    expect(wrapper.emitted('close')).toBeFalsy()
    headerClose().click()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('never gets initial focus onto the disabled input', () => {
    mountQuickOpen()
    expect(document.activeElement).not.toBe(document.body.querySelector('.fg-dialog-body input'))
  })
})

/* — 44: NotificationCenter AI detail — */

describe('NotificationCenter AI detail (NR0011 #44, §2.3-6)', () => {
  /**
   * The bespoke listener is removed rather than left beside the common one — two handlers for
   * one ESC is a double close. The remaining `window` keydown handler is the notification
   * PANEL's, which is not a dialog and keeps its own.
   */
  it('no longer answers Escape for the dialog itself', () => {
    const text = source('src/main/components/NotificationCenter.vue')
    expect(text).toMatch(/function onKeyDown[\s\S]*?if \(detailOpen\.value\) return/)
    expect(text).not.toMatch(/if \(detailOpen\.value\) \{\s*closeAiDetail\(\)/)
  })

  /** Focus return is the layer's now — the hand-rolled `nextTick(() => …focus())` is gone. */
  it('hands focus return to the shell instead of restoring it by hand', () => {
    // The host still owns the trigger element and passes it down; the extracted dialog is what
    // hands it to the shell (0560 T0020).
    const text = source('src/main/components/NotificationCenter.vue')
    expect(text).toContain(':return-focus-to="detailReturnFocus"')
    expect(text).not.toContain('nextTick(() => detailReturnFocus')
    expect(source('src/main/components/NotificationAiDetailDialog.vue'))
      .toContain(':return-focus-to="returnFocusTo"')
  })

  /**
   * The dialog left this component's subtree, so a click inside it now reads as "outside the
   * notification centre". Without this guard the panel underneath closed itself the moment the
   * user touched the dialog.
   */
  it('stops the outside-click handler from closing the panel under the open dialog', () => {
    expect(source('src/main/components/NotificationCenter.vue'))
      .toMatch(/function onClickOutside[\s\S]*?if \(detailOpen\.value\) return/)
  })

  /**
   * §2.3-6 role assignment. `문서 열기` navigates away from an unfinished job → `aux`. `닫기`
   * is D0008 §3's third cancel meaning: nothing is committed and nothing is running, so it is a
   * `dismiss`, not a form cancel — and a read-only overlay has no action that completes it, so
   * the `btn-primary` 닫기 NR0005 §4.1 flagged has no primary position to inherit.
   */
  it('assigns aux/dismiss and keeps 닫기 out of the primary position', () => {
    // The footer moved into the extracted dialog with the markup that renders it (0560 T0020).
    const text = source('src/main/components/NotificationAiDetailDialog.vue')
    expect(text).toMatch(/id: 'open-document',[\s\S]*?role: 'aux',/)
    expect(text).toMatch(/id: 'close',[\s\S]*?role: 'dismiss',/)
    expect(text).not.toMatch(/id: 'close',[\s\S]*?role: 'primary',/)
  })
})

/* — 51 / 52: the two settings views — */

describe('settings create/edit modals (NR0011 #51 · #52)', () => {
  it('MessagesView paints [취소] [저장] through the common footer', async () => {
    const wrapper = track(mount(MessagesView, mountOptions()))
    const auth = useAuthStore()
    auth.permissions = ['project.settings.edit']
    await flushPromises()

    await wrapper.find('.btn-primary').trigger('click')
    await flushPromises()

    expect(roles()).toEqual(['cancel', 'primary'])
    expect(actionIds()).toEqual(['cancel', 'save'])
    // Empty message → the primary action is gated exactly as the old inline button was.
    expect(action('save').disabled).toBe(true)
  })

  it('CommandsView paints [취소] [저장] and now has a header X to close with', async () => {
    const wrapper = track(mount(CommandsView, mountOptions()))
    await flushPromises()

    await wrapper.find('.btn-primary').trigger('click')
    await flushPromises()

    expect(roles()).toEqual(['cancel', 'primary'])
    expect(actionIds()).toEqual(['cancel', 'save'])
    // §2.2-2 does not apply here — this instance HAS a footer. What it gained is the header
    // close D0008 §2 assigns to DialogHeader; before the migration its title bar had none.
    expect(headerClose()).not.toBeNull()

    headerClose().click()
    await flushPromises()
    expect(surface()).toBeNull()
  })
})
