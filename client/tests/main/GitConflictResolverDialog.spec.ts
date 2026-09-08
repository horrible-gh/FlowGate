// flowgate.default.0212 T0009 — the shared conflict resolver dialog must carry
// the full 0207 시안 A UX: file sidebar with per-file state, chunk chips +
// prev/next navigation, AI assist strip with per-chunk recommendation badges,
// common-block folding, font-size controls and the residual-marker submit gate.
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import {
  parseConflictFile,
  type ConflictFileState,
} from '@main/composables/useConflictChunks'

// jsdom has no Element.scrollTo; focusChunk scrolls the chunk container.
beforeEach(() => {
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'en'
})

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

    const tabs = wrapper.findAll('.git-conflict-file-tab')
    expect(tabs).toHaveLength(2)
    expect(tabs[0].text()).toContain('git_service.py')
    expect(tabs[0].classes()).toContain('active')

    expect(wrapper.find('.git-ai-assist-strip').exists()).toBe(true)
    // 2 of the 3 chunks are safely recommendable (empty side + identical sides).
    expect(wrapper.find('.git-ai-assist-strip').text()).toContain('2')

    // file A has two chunks -> two numbered chips + prev/next buttons.
    expect(wrapper.findAll('.git-conflict-chip')).toHaveLength(2)
    expect(wrapper.find('.git-conflict-navigator').exists()).toBe(true)
    expect(wrapper.find('.git-code-size-controls').exists()).toBe(true)

    // progress indicator starts at 0 resolved of 3 total chunks.
    expect(wrapper.find('.git-conflict-progress').text()).toContain('0 / 3')

    wrapper.unmount()
  })

  it('marks recommendable chunks with AI badges and holds ambiguous ones', () => {
    const { wrapper } = mountDialog()

    const chunks = wrapper.findAll('.git-conflict-chunk')
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
    const chunks = wrapper.findAll('.git-conflict-chunk')
    const ambiguousChunk = chunks[1]

    expect(ambiguousChunk.findAll('.git-code-line.diff-changed')).toHaveLength(2)
    expect(ambiguousChunk.findAll('.git-code-token.diff-token-changed').length).toBeGreaterThan(0)

    wrapper.unmount()
  })

  it('folds long common blocks and expands them on demand', async () => {
    const { wrapper } = mountDialog()

    // the 14-line common head starts collapsed (> 12-line threshold); the
    // short 'between'/'tail' commons render inline without a toggle.
    const toggle = wrapper.find('.git-common-toggle')
    expect(toggle.exists()).toBe(true)
    expect(wrapper.text()).not.toContain('common head 1')

    await toggle.trigger('click')
    expect(wrapper.text()).toContain('common head 1')
    // and it collapses back.
    await wrapper.find('.git-common-toggle--open').trigger('click')
    expect(wrapper.text()).not.toContain('common head 1')

    wrapper.unmount()
  })

  it('adjusts the code font size with the A−/A＋ controls', async () => {
    const { wrapper } = mountDialog()

    const [down, up] = wrapper.findAll('.git-code-size-controls button')
    expect(wrapper.find('.git-code-size-controls').text()).toContain('86%')
    await up.trigger('click')
    expect(wrapper.find('.git-code-size-controls').text()).toContain('94%')
    expect(wrapper.find('.git-chunk-scroll').attributes('style')).toContain('0.94rem')
    await down.trigger('click')
    expect(wrapper.find('.git-code-size-controls').text()).toContain('86%')

    wrapper.unmount()
  })

  it('apply-all resolves only recommendable chunks; submit stays gated until every marker is gone', async () => {
    const { wrapper, files } = mountDialog()

    const submit = () => wrapper.find('.git-conflict-footer-actions .btn-primary')
    expect(submit().attributes('disabled')).toBeDefined()

    await wrapper.find('.git-ai-assist-strip .btn').trigger('click')

    // chunk 1 of file A and the file B chunk resolved; ambiguous chunk 2 pending.
    const segsA = files[0].segments.filter((s) => s.kind === 'chunk') as any[]
    expect(segsA[0].resolution).toBeTruthy()
    expect(segsA[1].resolution).toBeNull()
    const segsB = files[1].segments.filter((s) => s.kind === 'chunk') as any[]
    expect(segsB[0].resolution).toBeTruthy()
    expect(wrapper.find('.git-conflict-progress').text()).toContain('2 / 3')
    expect(submit().attributes('disabled')).toBeDefined()

    // human resolves the held chunk -> gate opens -> submit emits.
    const pendingChunk = wrapper.findAll('.git-conflict-chunk')[1]
    const [, theirsBtn] = pendingChunk.findAll('.git-chunk-actions button')
    await theirsBtn.trigger('click')
    expect(wrapper.find('.git-conflict-progress').text()).toContain('3 / 3')
    expect(submit().attributes('disabled')).toBeUndefined()

    await submit().trigger('click')
    expect(wrapper.emitted('submit')).toHaveLength(1)

    wrapper.unmount()
  })

  it('undo restores an applied choice and re-gates submit', async () => {
    const { wrapper, files } = mountDialog()

    await wrapper.find('.git-ai-apply').trigger('click')
    const segsA = files[0].segments.filter((s) => s.kind === 'chunk') as any[]
    expect(segsA[0].resolution).toBeTruthy()

    await wrapper.find('.git-chunk-undo').trigger('click')
    expect(segsA[0].resolution).toBeNull()
    expect(segsA[0].choice).toBeNull()

    wrapper.unmount()
  })

  it('switches files from the sidebar and reflects per-file resolution state', async () => {
    const { wrapper } = mountDialog()

    const tabs = wrapper.findAll('.git-conflict-file-tab')
    await tabs[1].trigger('click')
    expect(tabs[1].classes()).toContain('active')
    // file B has a single chunk.
    expect(wrapper.findAll('.git-conflict-chip')).toHaveLength(1)
    expect(wrapper.find('.git-conflict-selected-path').text()).toContain('GitStatusPanel.vue')

    wrapper.unmount()
  })

  it('emits the trimmed delivery message with the AI invocation', async () => {
    const { wrapper } = mountDialog()

    const message = wrapper.get('[data-test="conflict-ai-message"]')
    await message.setValue('  Preserve the current source and resolve only the markers.  ')
    const invoke = wrapper.findAll('.git-conflict-footer-actions .btn-secondary')
      .find(button => button.text().includes('Call AI'))
    expect(invoke).toBeTruthy()
    await invoke!.trigger('click')

    // 0481 D0006 §6.2 / L0007 §2.2 — [자동] rides along as the second argument,
    // read fresh from the footer checkbox at the moment [AI 호출] is pressed.
    expect(wrapper.emitted('ai-invoke')).toEqual([
      ['Preserve the current source and resolve only the markers.', false],
    ])

    wrapper.unmount()
  })

  it('checking [자동] sends auto=true with AI 호출 and 해소 제출, and resets on a fresh files load', async () => {
    const { wrapper, files } = mountDialog()

    await wrapper.find('.git-conflict-auto-toggle input').setValue(true)
    const invoke = wrapper.findAll('.git-conflict-footer-actions .btn-secondary')
      .find(button => button.text().includes('Call AI'))
    await invoke!.trigger('click')
    expect(wrapper.emitted('ai-invoke')?.[0]).toEqual(['', true])

    // 0234-shared submit gate: resolve everything so the primary button is enabled.
    await wrapper.find('.git-ai-assist-strip .btn').trigger('click')
    const pendingChunk = wrapper.findAll('.git-conflict-chunk')[1]
    const [, theirsBtn] = pendingChunk.findAll('.git-chunk-actions button')
    await theirsBtn.trigger('click')
    await wrapper.find('.git-conflict-footer-actions .btn-primary').trigger('click')
    expect(wrapper.emitted('submit')?.[0]).toEqual([true])

    // A fresh files prop (re-fetch/re-open) must not carry the checkbox forward.
    await wrapper.setProps({ files: [...files] })
    expect((wrapper.find('.git-conflict-auto-toggle input').element as HTMLInputElement).checked).toBe(false)

    wrapper.unmount()
  })

  it('emits close/abort/retry to the host', async () => {
    const { wrapper } = mountDialog({ loadStatus: 'error', errorMessage: 'boom' })

    expect(wrapper.find('.git-conflict-load-error').text()).toContain('boom')
    await wrapper.find('.git-conflict-load-error .btn').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)

    await wrapper.find('.git-dialog-close').trigger('click')
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

    const chunk = wrapper.find('.git-conflict-chunk')
    expect(chunk.exists()).toBe(true)

    // Verify base panel is rendered
    const sides = wrapper.findAll('.git-conflict-side')
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

    const chunk = wrapper.find('.git-conflict-chunk')
    expect(chunk.exists()).toBe(true)

    // Verify only ours and theirs panels are rendered (no base)
    const sides = wrapper.findAll('.git-conflict-side')
    expect(sides).toHaveLength(2) // ours, theirs
    expect(sides.filter((s) => s.classes().includes('base'))).toHaveLength(0)

    wrapper.unmount()
  })
})

// flowgate.default.0481 T0010 #2/#3/#4 — "아무 액션도 못하는 다이얼로그만 계속뜨고".
describe('GitConflictResolverDialog — the action bar is never absent', () => {
  function actionLabels(wrapper: ReturnType<typeof mountDialog>['wrapper']): string[] {
    return wrapper
      .findAll('.git-conflict-footer-actions button')
      .map((b) => b.text().trim())
  }

  it('keeps every action when the conflict list comes back empty', () => {
    // The dialog opens on a session the panel still calls `conflict`, but the list is empty
    // (already staged, or a moment stale). This used to render one sentence and the ✕ — the
    // operator could neither retry, nor call the AI, nor abort the merge.
    const { wrapper } = mountDialog({ files: [] })

    expect(wrapper.find('.git-conflict-empty').exists()).toBe(true)
    // a way to re-read the list, right where the emptiness is reported
    expect(wrapper.find('.git-conflict-empty button').exists()).toBe(true)
    // and the whole action bar, not a subset
    expect(actionLabels(wrapper)).toHaveLength(4)
    const buttons = wrapper.findAll('.git-conflict-footer-actions button')
    // [멘트 복사] / [AI 호출] / [중단] stay usable; only [해결 제출] has nothing to submit.
    expect(buttons.slice(0, 3).every((b) => b.attributes('disabled') === undefined)).toBe(true)
    expect(buttons[3].attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('emits ai-invoke and abort from the empty state', async () => {
    const { wrapper } = mountDialog({ files: [] })
    const buttons = wrapper.findAll('.git-conflict-footer-actions button')

    await buttons[1].trigger('click')
    await buttons[2].trigger('click')

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

    expect(actionLabels(wrapper)).toHaveLength(4)
    // the guard line is the footer's one sentence — it has to speak for this state too
    expect(wrapper.find('.git-conflict-guard').text()).toContain('boom')
    wrapper.unmount()
  })

  it('disables the AI call instead of deleting the whole action bar when no provider loaded', () => {
    // The entire footer-actions group used to hang off `v-if="providers?.length"`, so a
    // failed/empty provider list took [중단] and [해결 제출] with it — a dialog with a file
    // list and no way to act on it.
    const { wrapper } = mountDialog({ providers: [], selectedProvider: '' })

    const buttons = wrapper.findAll('.git-conflict-footer-actions button')
    expect(buttons).toHaveLength(4)
    expect(buttons[1].attributes('disabled')).toBeDefined()  // [AI 호출]
    expect(buttons[1].attributes('title')).toBeTruthy()      // …and it says why
    expect(buttons[2].attributes('disabled')).toBeUndefined() // [중단]
    wrapper.unmount()
  })

  it('lays the footer out the way mockup v13 화면 1 does', () => {
    // v13: `.git-conflict-footer-context` = 마커 가드 + 세로 구분선 + AI 호출 옵션(공급자
    // 셀렉트 · [자동]); `.git-conflict-footer-actions` = 네 버튼만.
    const { wrapper } = mountDialog()

    const context = wrapper.find('.git-conflict-dialog-ft > .git-conflict-footer-context')
    expect(context.exists()).toBe(true)
    expect(context.find('.git-conflict-guard').exists()).toBe(true)
    expect(context.find('.ft-divider').exists()).toBe(true)

    const options = context.find('.git-conflict-invoke-options')
    expect(options.exists()).toBe(true)
    expect(options.find('.git-conflict-provider').exists()).toBe(true)
    expect(options.find('.git-conflict-auto-toggle input').exists()).toBe(true)

    // the options moved OUT of the button group — the group is the four buttons, nothing else
    const actions = wrapper.find('.git-conflict-dialog-ft > .git-conflict-footer-actions')
    expect(actions.findAll('button')).toHaveLength(4)
    expect(actions.find('.git-conflict-provider').exists()).toBe(false)
    expect(actions.find('.git-conflict-auto-toggle').exists()).toBe(false)

    wrapper.unmount()
  })
})
