import { DOMWrapper, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import {
  parseConflictFile,
  type ConflictFileState,
} from '@main/composables/useConflictChunks'

const CONTENT = [
  'head',
  '<<<<<<< HEAD',
  'ours',
  '=======',
  'theirs',
  '>>>>>>> main',
  'tail',
].join('\n')

beforeEach(() => {
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'en'
})

afterEach(() => {
  resetDialogSystem()
})

function root(): DOMWrapper<HTMLElement> {
  return new DOMWrapper(document.body)
}

function makeFile(mode: ConflictFileState['mode'] = 'chunk'): ConflictFileState {
  const segments = parseConflictFile(CONTENT)
  if (!segments) throw new Error('fixture must parse')
  return {
    path: 'src/conflict.ts',
    conflict_count: 1,
    directText: CONTENT,
    mode,
    segments,
    notice: '',
  }
}

function mountDialog(file: ConflictFileState) {
  return mount(GitConflictResolverDialog, {
    props: {
      files: [file],
      branch: 'group/0590',
      baseBranch: 'main',
      busy: false,
      loadStatus: 'ready',
      errorMessage: '',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
    },
    global: {
      plugins: [i18n],
      stubs: { AppIcon: true },
    },
    attachTo: document.body,
  })
}

function gutterLast(): string {
  const text = root().get('.git-conflict-direct-editor .line-numbered-textarea__gutter').element.textContent ?? ''
  return text.split('\n').at(-1) ?? ''
}

describe('GitConflictResolverDialog direct editor line numbers (0590)', () => {
  it('keeps line numbers through chunk → direct → chunk → direct and live edits', async () => {
    const file = makeFile()
    const wrapper = mountDialog(file)

    await root().findAll('.git-conflict-mode-tabs button')[1].trigger('click')
    expect(root().get('.git-conflict-direct-editor textarea').attributes('wrap')).toBe('off')
    expect(gutterLast()).toBe(String(CONTENT.split('\n').length))

    await root().get('.git-conflict-direct-editor textarea').setValue(`${CONTENT}\nextra`)
    expect(file.directText).toBe(`${CONTENT}\nextra`)
    expect(gutterLast()).toBe(String(CONTENT.split('\n').length + 1))

    await root().findAll('.git-conflict-mode-tabs button')[0].trigger('click')
    await root().findAll('.git-conflict-mode-tabs button')[1].trigger('click')
    expect(gutterLast()).toBe(String(CONTENT.split('\n').length + 1))

    wrapper.unmount()
  })

  it('provides the same gutter when parser fallback forces direct_only mode', () => {
    const wrapper = mountDialog(makeFile('direct_only'))

    expect(root().find('.git-conflict-mode-tabs').exists()).toBe(false)
    expect(root().find('.git-conflict-direct-editor').exists()).toBe(true)
    expect(gutterLast()).toBe(String(CONTENT.split('\n').length))

    wrapper.unmount()
  })
})
