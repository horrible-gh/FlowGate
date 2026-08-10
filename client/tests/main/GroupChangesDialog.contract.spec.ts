import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import i18n from '../../shared/i18n'
import en from '../../shared/i18n/en'
import ja from '../../shared/i18n/ja'
import ko from '../../shared/i18n/ko'

/**
 * flowgate.default.0329 R0001/NR0003 — [변경사항 열기] 계약 가드.
 *
 * GroupChangesDialog.spec.ts는 mock 응답을 스스로 만들어 넣기 때문에, mock만 새 계약으로
 * 바꿔놓고 컴포넌트가 옛 계약(0325판 hunks)을 계속 읽고 있어도 그 스펙만으로는 잡히지
 * 않는다 — 실제로 DocInfoPanel.acChanges.spec.ts가 자기 mock을 따로 들고 있었던 탓에
 * 스펙은 초록인데 화면은 `Cannot read properties of undefined (reading 'binary')`로
 * 죽는 상태가 있었다.
 *
 * 0394 T0016 (NR0003 §6.2-라): 그 대응으로 이 파일은 컴포넌트 소스를 읽어
 * `expect(source).toContain("from '../composables/useFileDiff'")`,
 * `expect(source).not.toContain('diff.hunks')` 처럼 **텍스트로** 단언했다. 그런데 그 방식은
 * 잡으려던 것을 잡지 못한다 — `diff.hunks` 대신 `diff['hunks']`나 구조분해로 읽으면 문자열은
 * 사라지지만 화면은 똑같이 죽고, 반대로 import 줄만 남기고 자체 differ를 다시 짜 넣어도
 * 전부 초록이다. 그래서 두 케이스를 **마운트 결과**로 바꿨다: 응답의 old/new 내용으로 그린
 * 화면이 공용 엔진이 같은 입력에서 내는 결과와 글자 단위로 같은지, 그리고 binary/truncated를
 * 각 면에서 읽는지(=0325판 최상위 필드를 보지 않는지)를 확인한다.
 */

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

import GroupChangesDialog from '@main/components/GroupChangesDialog.vue'
import {
  buildDiffRows,
  collapseCommonRows,
  splitTextLines,
  toUnifiedRows,
} from '@main/composables/useFileDiff'

const CLIENT_DIR = resolve(__dirname, '../..')
const EXPLORER_STORE = resolve(CLIENT_DIR, 'src/main/stores/explorer.ts')

// TC-4가 파일 경로 목록으로 vitest를 돌리는데, vitest는 매칭되는 파일이 하나도 없어도
// 조용히 통과(exit 0)한다. 경로가 옮겨지면 그 케이스가 무증상으로 비는 것을 여기서 막는다.
//
// 0394 T0004 (NR0003 §8.3 / §13-14): 실제로 옮겼고, 그래서 이 가드가 걸렸다 — 설계대로
// 동작한 것이다. 클라이언트 테스트가 `client/tests/**` 와 `client/src/**/__tests__/**`
// 두 뿌리에 나뉘어 있던 것을 한 곳(`client/tests/`)으로 모으면서 두 파일도 함께 옮겼다.
//
// ⚠️ 이 목록을 고치는 것만으로는 끝이 아니다. 같은 경로를 **저장소 밖의 TC-4 시나리오
// 문서가 명령줄에 그대로 적어** 두고 있고, 그쪽은 없는 경로를 받아도 exit 0 이라 아무
// 증상 없이 0건을 돌린다. TC-4 의 명령을 아래 경로로 함께 갱신해야 한다.
const SHARED_DIFF_ENGINE_SPECS = [
  'tests/main/useFileDiff.spec.ts',
  'tests/main/FileDiffViewer.spec.ts',
]

const FILE = 'server/services/git_service.py'
const OLD_CONTENT = 'keep one\nold line\nkeep two\n'
const NEW_CONTENT = 'keep one\nnew line\nextra line\nkeep two\n'

const CHANGES = [{ path: FILE, status: 'M', insertions: 2, deletions: 1 }]

/** The 0326 NR0005 §4 response: two content sides, each with its own flags. */
function sideDiff(overrides: Record<string, unknown> = {}) {
  return {
    path: FILE,
    status: 'M',
    old: { exists: true, binary: false, truncated: false, size: OLD_CONTENT.length, content: OLD_CONTENT },
    new: { exists: true, binary: false, truncated: false, size: NEW_CONTENT.length, content: NEW_CONTENT },
    ...overrides,
  }
}

function mountDialog() {
  return mount(GroupChangesDialog, {
    props: {
      projectId: 'flowgate',
      groupId: 'flowgate.default.0329',
      branch: 'flowgate_default_0329',
      baseBranch: 'main',
      changes: CHANGES,
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

async function openWith(payload: Record<string, unknown>) {
  getRequest.mockResolvedValue({ data: { data: payload } })
  const wrapper = mountDialog()
  await flushPromises()
  return wrapper
}

/** What the shared engine derives for the same two sides, as rendered text. */
function engineUnifiedLines(oldContent: string, newContent: string): string[] {
  const { rows } = buildDiffRows(splitTextLines(oldContent), splitTextLines(newContent))
  return collapseCommonRows(rows).flatMap((section) =>
    section.kind === 'gap'
      ? []
      // `.text()` trims, and a context row's sign is a single space.
      : toUnifiedRows(section.rows).map((row) => `${row.sign.trim()}${row.line.line}`),
  )
}

function groupChanges(messages: Record<string, any>): Record<string, unknown> {
  return messages.main.group_changes as Record<string, unknown>
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  i18n.global.locale.value = 'en'
})

describe('group changes dialog — 0326 old/new-content contract', () => {
  it.each([
    ['ko', ko],
    ['en', en],
    ['ja', ja],
  ])('%s renamed untracked_note to added_note and dropped oversized', (_, messages) => {
    const bundle = groupChanges(messages as Record<string, any>)
    // 0326 계약은 "커밋됐는지"를 알려주지 않는다 — 신규 추가(status 'A')만 말할 수 있다.
    expect(typeof bundle.added_note).toBe('string')
    expect(bundle.untracked_note).toBeUndefined()
    // 서버가 파일을 통째로 건너뛰던 0325 전용 상태. 이제 발생하지 않는다.
    expect(bundle.oversized).toBeUndefined()
  })

  it('renders the diff the shared useFileDiff engine derives, line for line', async () => {
    const wrapper = await openWith(sideDiff())

    const rendered = wrapper.findAll('.gcd-line').map((row) => {
      const cells = row.findAll('span')
      return `${cells[2].text()}${cells[3].text()}`
    })

    // A private differ would have to reproduce the shared engine exactly — sign, order,
    // context and all — to satisfy this; a server-side hunk parser could not, because the
    // response carries no hunks to parse.
    expect(rendered).toEqual(engineUnifiedLines(OLD_CONTENT, NEW_CONTENT))
    expect(rendered).toContain('-old line')
    expect(rendered).toContain('+new line')
    expect(rendered).toContain('+extra line')
    wrapper.unmount()
  })

  it('reads binary and truncated off each side, not off the response root', async () => {
    // The 0325-only fields are attached at the root here. They no longer exist on the live
    // response, so a component that still reads them would either honour these (and hide a
    // perfectly readable diff) or crash on the sides it never looks at.
    const withDeadRootFields = await openWith(
      sideDiff({ binary: true, truncated: true, oversized: true, untracked: true, hunks: [], insertions: 99, deletions: 99 }),
    )
    expect(withDeadRootFields.findAll('.gcd-line').length).toBeGreaterThan(0)
    expect(withDeadRootFields.find('.gcd-diff-state').exists()).toBe(false)
    // +/− come from the changes row already in hand, not from the response.
    expect(withDeadRootFields.find('.gcd-diff-lines').text()).toContain('+2')
    withDeadRootFields.unmount()

    // ...and the side-level flags ARE honoured.
    const binarySide = await openWith(
      sideDiff({ new: { exists: true, binary: true, truncated: false, size: 10, content: null } }),
    )
    expect(binarySide.find('.gcd-diff-state').text()).toBe(i18n.global.t('main.group_changes.binary'))
    binarySide.unmount()

    const truncatedSide = await openWith(
      sideDiff({ new: { exists: true, binary: false, truncated: true, size: 10, content: NEW_CONTENT } }),
    )
    expect(truncatedSide.find('.gcd-notice-warn').exists()).toBe(true)
    truncatedSide.unmount()
  })

  it('types GroupFileDiffData as two content sides', () => {
    // (A) 유지: 타입은 런타임에 남지 않으므로 마운트로는 관찰할 수 없다. 여기서 지키는 것은
    // "삭제한 0325 타입이 되살아나 계약이 두 벌이 되는 것"이며, 그 실패는 vue-tsc 가 아니라
    // 사람의 눈에만 보인다 — 되살아난 타입은 그 자체로는 컴파일 오류가 아니기 때문이다.
    const source = readFileSync(EXPLORER_STORE, 'utf8')
    expect(source).toContain('export interface GroupDiffSide')
    for (const field of ['exists', 'binary', 'truncated', 'content']) {
      expect(source).toContain(field)
    }
    expect(source).toMatch(/interface GroupFileDiffData[\s\S]*?\bstatus:\s*'M'\s*\|\s*'A'\s*\|\s*'D'/)
    expect(source).toMatch(/interface GroupFileDiffData[\s\S]*?\bold:\s*GroupDiffSide/)
    expect(source).toMatch(/interface GroupFileDiffData[\s\S]*?\bnew:\s*GroupDiffSide/)
    expect(source).not.toContain('export interface GroupDiffHunk')
    expect(source).not.toContain('export interface GroupDiffLine')
  })

  it.each(SHARED_DIFF_ENGINE_SPECS)('shared diff engine spec %s still exists', (relative) => {
    expect(existsSync(resolve(CLIENT_DIR, relative))).toBe(true)
  })
})
