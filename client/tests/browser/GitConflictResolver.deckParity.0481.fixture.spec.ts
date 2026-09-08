// flowgate.default.0481 T0010 #4 ("시안대로는 똑바로 되어있지도 않은거같고") — export the
// REAL resolver dialog DOM so conflict-resolver-deck.0481.mjs can measure it against 시안
// 덱 y5bwr1o0 v13 화면 1 in headless Chrome with the production CSS bundle. Same shape as
// GitStatusPanel.geometry.fixture.spec.ts (0482): the component renders here, the judging
// happens there.
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import { parseConflictFile, type ConflictFileState } from '@main/composables/useConflictChunks'

vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const CONTENT = ['head', '<<<<<<< HEAD', 'ours', '=======', 'theirs', '>>>>>>> main', 'tail'].join('\n')

function makeFile(path: string): ConflictFileState {
  const segments = parseConflictFile(CONTENT)
  if (!segments) throw new Error('fixture must parse')
  return { path, conflict_count: 1, directText: CONTENT, mode: 'chunk', segments, notice: '' }
}

function dump(name: string, props: Record<string, unknown>) {
  const wrapper = mount(GitConflictResolverDialog, {
    props: {
      files: [makeFile('server/modules/flow_gate/services/git_service.py')],
      branch: 'feature/0481', baseBranch: 'main', busy: false,
      loadStatus: 'ready', errorMessage: '',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }], selectedProvider: 'p1',
      ...props,
    },
    global: { plugins: [i18n], stubs: { AppIcon: true } },
  })
  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(resolve(scratch, `conflict-resolver.${name}.html`), wrapper.html(), 'utf8')
  wrapper.unmount()
}

it('exports the resolver dialog DOM for the v13 화면 1 deck comparison', () => {
  i18n.global.locale.value = 'ko'
  dump('ready', {})
  // The two states T0010 #2 was about: an empty list, and a list that never loaded. Both
  // must still carry the same action bar as `ready`.
  dump('empty', { files: [] })
  dump('error', { files: [], loadStatus: 'error', errorMessage: '충돌 목록을 불러오지 못했습니다.' })
  expect(true).toBe(true)
})
