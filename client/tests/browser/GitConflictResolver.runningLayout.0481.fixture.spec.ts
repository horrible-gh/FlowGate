// flowgate.default.0481 T0010 rev5 (반려 #2) — "실행하면 뒈지게 밉게 바뀜 / README.md: 16,
// 28행 가 세로로 나옴 자리 다 차지함". Export the REAL resolver dialog DOM in the state the
// reviewer photographed — a file whose markers still sit on lines 16 and 28, and this
// group's own conflict AI run at 0:28 — so conflict-resolver-running.0481.mjs can MEASURE
// the footer in headless Chrome with the production CSS bundle instead of arguing about it.
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import { parseConflictFile, residualMarkers, type ConflictFileState } from '@main/composables/useConflictChunks'

vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

// 15 lines of context, the open marker on line 16, the close marker on line 28 — the exact
// pair the guard sentence in the screenshot was reporting.
const CONTENT = [
  ...Array.from({ length: 15 }, (_, i) => `# heading ${i + 1}`),
  '<<<<<<< HEAD',
  ...Array.from({ length: 5 }, (_, i) => `ours ${i + 1}`),
  '=======',
  ...Array.from({ length: 5 }, (_, i) => `theirs ${i + 1}`),
  '>>>>>>> main',
  'tail',
].join('\n')

function makeFile(path: string): ConflictFileState {
  const segments = parseConflictFile(CONTENT)
  if (!segments) throw new Error('fixture must parse')
  return { path, conflict_count: 1, directText: CONTENT, mode: 'chunk', segments, notice: '' }
}

function dump(name: string, props: Record<string, unknown>) {
  const wrapper = mount(GitConflictResolverDialog, {
    props: {
      files: [makeFile('README.md')],
      branch: 'test2_default_0005', baseBranch: 'main', busy: false,
      loadStatus: 'ready', errorMessage: '',
      providers: [{ id: 'p1', name: 'Claude Haiku 4.5' }], selectedProvider: 'p1',
      ...props,
    },
    global: { plugins: [i18n], stubs: { AppIcon: true } },
  })
  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(resolve(scratch, `conflict-resolver-running.${name}.html`), wrapper.html(), 'utf8')
  wrapper.unmount()
}

it('the fixture really is the reported guard sentence', () => {
  // If this ever stops saying [16, 28] the harness below is measuring some other screen.
  expect(residualMarkers(CONTENT)).toEqual([16, 28])
})

it('exports the running / blocked / idle resolver DOM for the layout measurement', () => {
  i18n.global.locale.value = 'ko'
  dump('running', {
    aiRunNotice: 'Claude Haiku 4.5 이(가) 충돌을 해소하는 중입니다 · 0:28 경과 — 끝나면 이 화면이 결과로 바뀝니다.',
  })
  // 반려 #1: the press is visible before any run entry exists.
  dump('starting', { aiRunPending: true })
  // 반려 #4: the empty provider list says which condition it is, in a sentence.
  dump('noprovider', { providers: [], selectedProvider: '' })
  dump('providerloading', { providers: [], selectedProvider: '', providerLoading: true })
  dump('providererror', { providers: [], selectedProvider: '', providerErrored: true })
  // The deck's own state: no strip at all.
  dump('idle', {})
})
