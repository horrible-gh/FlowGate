// flowgate.default.0212 T0009 — the shared conflict resolver dialog must carry
// the full 0207 시안 A UX: file sidebar with per-file state, chunk chips +
// prev/next navigation, AI assist strip with per-chunk recommendation badges,
// common-block folding, font-size controls and the residual-marker submit gate.
//
// flowgate.default.0560 T0024 — the dialog moved onto the common layer, so every query
// below reads the TELEPORTED DOM (`DialogShell` mounts on `#dialog-root` / `document.body`,
// not inside this component's subtree) and the footer is `DialogFooter`'s four semantic
// actions instead of a hand-built `.git-conflict-footer-actions` row. What is asserted did
// not change: the body behaviour cases are the originals, untouched apart from the root
// they look in — that is the point of "body는 공통화하지 않는다" (T0024 §2.3).
import { DOMWrapper, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import {
  parseConflictFile,
  type ConflictFileState,
} from '@main/composables/useConflictChunks'

// jsdom has no Element.scrollTo; focusChunk scrolls the chunk container.
beforeEach(() => {
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'en'
})

afterEach(() => {
  resetDialogSystem()
})

/** The dialog's real root: `DialogShell` teleports the whole surface out of the wrapper. */
function root(): DOMWrapper<HTMLElement> {
  return new DOMWrapper(document.body)
}

/** One footer action's `<button>`, found the way `DialogFooter` labels it. */
function action(id: string): DOMWrapper<HTMLButtonElement> {
  return root().find<HTMLButtonElement>(`[data-dialog-action-id="${id}"]`)
}

function footerButtons(): DOMWrapper<HTMLButtonElement>[] {
  return root().findAll<HTMLButtonElement>('.fg-dialog-footer__actions button')
}

function actionIds(): string[] {
  return footerButtons().map((b) => b.attributes('data-dialog-action-id') ?? '')
}

function actionRoles(): string[] {
  return footerButtons().map((b) => b.attributes('data-dialog-action-role') ?? '')
}

const FILE_A_CONTENT = [
  ...Array.from({ length: 14 }, (_, i) => `common head ${i + 1}`),
  '<<<<<<< HEAD',
  'keep me',
  '=======',
  '>>>>>>> main',
  'between',
  '<<<<<<< HEAD',
  'alpha',
  '=======',
  'beta',
  '>>>>>>> main',
  'tail',
].join('\n')

const FILE_B_CONTENT = ['x', '<<<<<<< HEAD', 'same', '=======', 'same', '>>>>>>> main', 'y'].join('\n')

function makeFile(path: string, content: string, conflictCount: number): ConflictFileState {
  const segments = parseConflictFile(content)
  if (!segments) throw new Error('fixture must parse: ' + path)
  return {
    path,
    conflict_count: conflictCount,
    directText: content,
    mode: 'chunk',
    segments,
    notice: '',
  }
}

function mountDialog(overrides: Record<string, unknown> = {}) {
  const files = [
    makeFile('server/app/git_service.py', FILE_A_CONTENT, 2),
    makeFile('client/src/GitStatusPanel.vue', FILE_B_CONTENT, 1),
  ]
  const wrapper = mount(GitConflictResolverDialog, {
    props: {
      files,
      branch: 'group/0212',
      baseBranch: 'main',
      busy: false,
      loadStatus: 'ready',
      errorMessage: '',
      // 0234 B0001 RC1/RC2 added the footer provider select gated behind
      // `providers?.length` — without a fixture here the footer-actions block
      // (and everything in it: copy-mention/AI-invoke/abort/submit) never
      // renders, which silently broke every test below that queries it.
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
      ...overrides,
    },
    global: {
      plugins: [i18n],
      stubs: { AppIcon: true },
    },
    attachTo: document.body,
  })
  return { wrapper, files }
}

describe('GitConflictResolverDialog (shared 0207 시안 A resolver)', () => {
  it('renders the file sidebar, AI assist strip, chunk chips and font controls', () => {
    const { wrapper } = mountDialog()

    const tabs = root().findAll('.git-conflict-file-tab')
    expect(tabs).toHaveLength(2)
    expect(tabs[0].text()).toContain('git_service.py')
    expect(tabs[0].classes()).toContain('active')

    expect(root().find('.git-ai-assist-strip').exists()).toBe(true)
    // 2 of the 3 chunks are safely recommendable (empty side + identical sides).
    expect(root().find('.git-ai-assist-strip').text()).toContain('2')

    // file A has two chunks -> two numbered chips + prev/next buttons.
    expect(root().findAll('.git-conflict-chip')).toHaveLength(2)
    expect(root().find('.git-conflict-navigator').exists()).toBe(true)
    expect(root().find('.git-code-size-controls').exists()).toBe(true)

    // progress indicator starts at 0 resolved of 3 total chunks.
    expect(root().find('.git-conflict-progress').text()).toContain('0 / 3')

    wrapper.unmount()
  })

  it('marks recommendable chunks with AI badges and holds ambiguous ones', () => {
    const { wrapper } = mountDialog()

    const chunks = root().findAll('.git-conflict-chunk')
    expect(chunks).toHaveLength(2) // file A is selected

    // chunk 1 (theirs empty -> recommend ours): suggested highlight + apply button.
    expect(chunks[0].find('.git-chunk-actions button.suggested').exists()).toBe(true)
    expect(chunks[0].find('.git-ai-apply').exists()).toBe(true)
    expect(chunks[0].find('.git-ai-recommended').exists()).toBe(true)

    // chunk 2 (alpha vs beta, no base): AI holds, human decides.
    expect(chunks[1].find('.git-ai-hold').exists()).toBe(true)
    expect(chunks[1].find('.git-ai-apply').exists()).toBe(false)

    wrapper.unmount()
  })

  it('renders line and token diff classes for unresolved chunk sides', () => {
    const { wrapper } = mountDialog()
    const chunks = root().findAll('.git-conflict-chunk')
    const ambiguousChunk = chunks[1]

    expect(ambiguousChunk.findAll('.git-code-line.diff-changed')).toHaveLength(2)
    expect(ambiguousChunk.findAll('.git-code-token.diff-token-changed').length).toBeGreaterThan(0)

    wrapper.unmount()
  })

  it('folds long common blocks and expands them on demand', async () => {
    const { wrapper } = mountDialog()

    // the 14-line common head starts collapsed (> 12-line threshold); the
    // short 'between'/'tail' commons render inline without a toggle.
    const toggle = root().find('.git-common-toggle')
    expect(toggle.exists()).toBe(true)
    expect(root().find('.git-conflict-body').text()).not.toContain('common head 1')

    await toggle.trigger('click')
    expect(root().find('.git-conflict-body').text()).toContain('common head 1')
    // and it collapses back.
    await root().find('.git-common-toggle--open').trigger('click')
    expect(root().find('.git-conflict-body').text()).not.toContain('common head 1')

    wrapper.unmount()
  })

  it('adjusts the code font size with the A−/A＋ controls', async () => {
    const { wrapper } = mountDialog()

    const [down, up] = root().findAll('.git-code-size-controls button')
    expect(root().find('.git-code-size-controls').text()).toContain('86%')
    await up.trigger('click')
    expect(root().find('.git-code-size-controls').text()).toContain('94%')
    expect(root().find('.git-chunk-scroll').attributes('style')).toContain('0.94rem')
    await down.trigger('click')
    expect(root().find('.git-code-size-controls').text()).toContain('86%')

    wrapper.unmount()
  })

  it('apply-all resolves only recommendable chunks; submit stays gated until every marker is gone', async () => {
    const { wrapper, files } = mountDialog()

    expect(action('submit').attributes('disabled')).toBeDefined()

    await root().find('.git-ai-assist-strip .btn').trigger('click')

    // chunk 1 of file A and the file B chunk resolved; ambiguous chunk 2 pending.
    const segsA = files[0].segments.filter((s) => s.kind === 'chunk') as any[]
    expect(segsA[0].resolution).toBeTruthy()
    expect(segsA[1].resolution).toBeNull()
    const segsB = files[1].segments.filter((s) => s.kind === 'chunk') as any[]
    expect(segsB[0].resolution).toBeTruthy()
    expect(root().find('.git-conflict-progress').text()).toContain('2 / 3')
    expect(action('submit').attributes('disabled')).toBeDefined()

    // human resolves the held chunk -> gate opens -> submit emits.
    const pendingChunk = root().findAll('.git-conflict-chunk')[1]
    const [, theirsBtn] = pendingChunk.findAll('.git-chunk-actions button')
    await theirsBtn.trigger('click')
    expect(root().find('.git-conflict-progress').text()).toContain('3 / 3')
    expect(action('submit').attributes('disabled')).toBeUndefined()

    await action('submit').trigger('click')
    expect(wrapper.emitted('submit')).toHaveLength(1)

    wrapper.unmount()
  })

  it('undo restores an applied choice and re-gates submit', async () => {
    const { wrapper, files } = mountDialog()

    await root().find('.git-ai-apply').trigger('click')
    const segsA = files[0].segments.filter((s) => s.kind === 'chunk') as any[]
    expect(segsA[0].resolution).toBeTruthy()

    await root().find('.git-chunk-undo').trigger('click')
    expect(segsA[0].resolution).toBeNull()
    expect(segsA[0].choice).toBeNull()

    wrapper.unmount()
  })

  it('switches files from the sidebar and reflects per-file resolution state', async () => {
    const { wrapper } = mountDialog()

    const tabs = root().findAll('.git-conflict-file-tab')
    await tabs[1].trigger('click')
    expect(tabs[1].classes()).toContain('active')
    // file B has a single chunk.
    expect(root().findAll('.git-conflict-chip')).toHaveLength(1)
    expect(root().find('.git-conflict-selected-path').text()).toContain('GitStatusPanel.vue')

    wrapper.unmount()
  })

  it('emits the trimmed delivery message with the AI invocation', async () => {
    const { wrapper } = mountDialog()

    const message = root().get('[data-test="conflict-ai-message"]')
    await message.setValue('  Preserve the current source and resolve only the markers.  ')
    await action('ai-invoke').trigger('click')

    // 0481 D0006 §6.2 / L0007 §2.2 — [자동] rides along as the second argument,
    // read fresh from the footer checkbox at the moment [AI 호출] is pressed.
    expect(wrapper.emitted('ai-invoke')).toEqual([
      ['Preserve the current source and resolve only the markers.', false],
    ])

    wrapper.unmount()
  })

  it('checking [자동] sends auto=true with AI 호출 and 해소 제출, and resets on a fresh files load', async () => {
    const { wrapper, files } = mountDialog()

    await root().find('.git-conflict-auto-toggle input').setValue(true)
    await action('ai-invoke').trigger('click')
    expect(wrapper.emitted('ai-invoke')?.[0]).toEqual(['', true])

    // 0234-shared submit gate: resolve everything so the primary button is enabled.
    await root().find('.git-ai-assist-strip .btn').trigger('click')
    const pendingChunk = root().findAll('.git-conflict-chunk')[1]
    const [, theirsBtn] = pendingChunk.findAll('.git-chunk-actions button')
    await theirsBtn.trigger('click')
    await action('submit').trigger('click')
    expect(wrapper.emitted('submit')?.[0]).toEqual([true])

    // A fresh files prop (re-fetch/re-open) must not carry the checkbox forward.
    await wrapper.setProps({ files: [...files] })
    expect((root().find('.git-conflict-auto-toggle input').element as HTMLInputElement).checked).toBe(false)

    wrapper.unmount()
  })

  it('emits close/abort/retry to the host', async () => {
    const { wrapper } = mountDialog({ loadStatus: 'error', errorMessage: 'boom' })

    expect(root().find('.git-conflict-load-error').text()).toContain('boom')
    await root().find('.git-conflict-load-error .btn').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)

    // 0560 T0024 §2.2 — the hand-written `.git-dialog-close` is `DialogHeader`'s ✕ now, and
    // it closes through `DialogShell.requestClose('header')` instead of emitting directly.
    await root().find('.fg-dialog-header__close').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
  })

  it('renders the base panel when zdiff3 base marker is present', () => {
    // Real zdiff3 conflict fixture from test_git_integration_0115.py::test_conflict_resolve_flow
    const baseMarkerContent = [
      'common line',
      '<<<<<<< HEAD',
      'mainline version',
      '||||||| line1',
      'line1',
      '=======',
      'group version',
      '>>>>>>> group/branch',
      'after',
    ].join('\n')

    const { wrapper } = mountDialog({
      files: [makeFile('conflict.txt', baseMarkerContent, 1)],
    })

    const chunk = root().find('.git-conflict-chunk')
    expect(chunk.exists()).toBe(true)

    // Verify base panel is rendered
    const sides = root().findAll('.git-conflict-side')
    expect(sides).toHaveLength(3) // ours, base, theirs
    expect(sides[1].classes()).toContain('base')

    // Verify base panel has correct label and content (translated label)
    const baseLabel = sides[1].find('.git-conflict-side-label').text().toLowerCase()
    expect(baseLabel).toContain('common')
    expect(sides[1].text()).toContain('line1')

    // 0484 T0009: the base column goes through baseDiff(), which diffs base
    // against itself — every line and token is 'common' (plain context, no
    // add/remove highlight) and still carries its own line number.
    const baseLines = sides[1].findAll('.git-code-line')
    expect(baseLines).toHaveLength(1)
    expect(baseLines[0].classes()).toContain('diff-common')
    expect(baseLines[0].find('.git-line-number').text()).toBe('5')
    const baseTokens = baseLines[0].findAll('.git-code-token')
    expect(baseTokens.length).toBeGreaterThan(0)
    expect(baseTokens.every((token) => token.classes().includes('diff-token-common'))).toBe(true)

    wrapper.unmount()
  })

  it('does not render base panel when base marker is absent', () => {
    const { wrapper } = mountDialog({
      files: [makeFile('conflict.txt', FILE_B_CONTENT, 1)],
    })

    const chunk = root().find('.git-conflict-chunk')
    expect(chunk.exists()).toBe(true)

    // Verify only ours and theirs panels are rendered (no base)
    const sides = root().findAll('.git-conflict-side')
    expect(sides).toHaveLength(2) // ours, theirs
    expect(sides.filter((s) => s.classes().includes('base'))).toHaveLength(0)

    wrapper.unmount()
  })
})

// flowgate.default.0481 T0010 #2/#3/#4 — "아무 액션도 못하는 다이얼로그만 계속뜨고".
describe('GitConflictResolverDialog — the action bar is never absent', () => {
  it('keeps every action when the conflict list comes back empty', () => {
    // The dialog opens on a session the panel still calls `conflict`, but the list is empty
    // (already staged, or a moment stale). This used to render one sentence and the ✕ — the
    // operator could neither retry, nor call the AI, nor abort the merge.
    const { wrapper } = mountDialog({ files: [] })

    expect(root().find('.git-conflict-empty').exists()).toBe(true)
    // a way to re-read the list, right where the emptiness is reported
    expect(root().find('.git-conflict-empty button').exists()).toBe(true)
    // and the whole action bar, not a subset
    expect(actionIds()).toEqual(['copy-mention', 'ai-invoke', 'abort', 'submit'])
    // [멘트 복사] / [AI 호출] / [중단] stay usable; only [해결 제출] has nothing to submit.
    expect(action('copy-mention').attributes('disabled')).toBeUndefined()
    expect(action('ai-invoke').attributes('disabled')).toBeUndefined()
    expect(action('abort').attributes('disabled')).toBeUndefined()
    expect(action('submit').attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('emits ai-invoke and abort from the empty state', async () => {
    const { wrapper } = mountDialog({ files: [] })

    await action('ai-invoke').trigger('click')
    await action('abort').trigger('click')

    expect(wrapper.emitted('ai-invoke')).toBeTruthy()
    expect(wrapper.emitted('abort')).toBeTruthy()
    wrapper.unmount()
  })

  it('keeps the action bar when the load failed', () => {
    const { wrapper } = mountDialog({
      files: [],
      loadStatus: 'error',
      errorMessage: 'boom',
    })

    expect(actionIds()).toHaveLength(4)
    // the guard line is the footer's one sentence — it has to speak for this state too
    expect(root().find('.git-conflict-guard').text()).toContain('boom')
    wrapper.unmount()
  })

  it('disables the AI call instead of deleting the whole action bar when no provider loaded', () => {
    // The entire footer-actions group used to hang off `v-if="providers?.length"`, so a
    // failed/empty provider list took [중단] and [해결 제출] with it — a dialog with a file
    // list and no way to act on it.
    const { wrapper } = mountDialog({ providers: [], selectedProvider: '' })

    expect(actionIds()).toHaveLength(4)
    expect(action('ai-invoke').attributes('disabled')).toBeDefined()   // [AI 호출]
    // …and it still says why. 0560 T0024 §2.5: the reason is the SENTENCE above the row
    // (`.git-conflict-ai-strip`, 0481 T0010 rev5), not a `title` attribute on the button —
    // `DialogAction` has no attribute channel and this T does not add one.
    expect(action('ai-invoke').attributes('title')).toBeUndefined()
    expect(root().find('.git-conflict-ai-strip').text().trim().length).toBeGreaterThan(0)
    expect(action('abort').attributes('disabled')).toBeUndefined()     // [중단]
    wrapper.unmount()
  })

  it('lays the footer out the way mockup v13 화면 1 does', () => {
    // v13: `.git-conflict-footer-context` = 마커 가드 + 세로 구분선 + AI 호출 옵션(공급자
    // 셀렉트 · [자동]); the button row = 네 버튼만.
    // 0560 T0024 §2.5: the band is `DialogFooter`'s SIBLING inside the surface now — the
    // hand-built `.git-conflict-dialog-ft` row that used to hold both is gone.
    const { wrapper } = mountDialog()

    const context = root().find('.fg-dialog-surface > .git-conflict-footer-context')
    expect(context.exists()).toBe(true)
    expect(context.find('.git-conflict-guard').exists()).toBe(true)
    expect(context.find('.ft-divider').exists()).toBe(true)

    const options = context.find('.git-conflict-invoke-options')
    expect(options.exists()).toBe(true)
    expect(options.find('.git-conflict-provider').exists()).toBe(true)
    expect(options.find('.git-conflict-auto-toggle input').exists()).toBe(true)

    // the options are not in the button group — the group is the four buttons, nothing else
    const actions = root().find('.fg-dialog-surface > .fg-dialog-footer')
    expect(actions.exists()).toBe(true)
    expect(actions.findAll('button')).toHaveLength(4)
    expect(actions.find('.git-conflict-provider').exists()).toBe(false)
    expect(actions.find('.git-conflict-auto-toggle').exists()).toBe(false)

    // DS0007's order, produced by role priority rather than by DOM order.
    expect(actionIds()).toEqual(['copy-mention', 'ai-invoke', 'abort', 'submit'])
    expect(actionRoles()).toEqual(['aux', 'aux', 'stop', 'primary'])

    wrapper.unmount()
  })
})
