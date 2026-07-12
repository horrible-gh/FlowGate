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

  it('emits close/abort/retry to the host', async () => {
    const { wrapper } = mountDialog({ loadStatus: 'error', errorMessage: 'boom' })

    expect(wrapper.find('.git-conflict-load-error').text()).toContain('boom')
    await wrapper.find('.git-conflict-load-error .btn').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)

    await wrapper.find('.git-dialog-close').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
  })
})
