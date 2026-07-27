import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

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
 * 죽는 상태가 있었다. 이 스펙은 mock을 거치지 않고 소스 자체를 읽어, 컴포넌트/타입/문구가
 * 0326 NR0005 §4의 old/new-content 계약 한 벌만 바라보는지 확인한다.
 */

const CLIENT_DIR = resolve(__dirname, '../..')
const DIALOG = resolve(CLIENT_DIR, 'src/main/components/GroupChangesDialog.vue')
const EXPLORER_STORE = resolve(CLIENT_DIR, 'src/main/stores/explorer.ts')

// TC-4가 파일 경로 목록으로 vitest를 돌리는데, vitest는 매칭되는 파일이 하나도 없어도
// 조용히 통과(exit 0)한다. 경로가 옮겨지면 그 케이스가 무증상으로 비는 것을 여기서 막는다.
const SHARED_DIFF_ENGINE_SPECS = [
  'src/main/composables/__tests__/useFileDiff.spec.ts',
  'src/main/components/__tests__/FileDiffViewer.spec.ts',
]

function readSource(path: string): string {
  return readFileSync(path, 'utf8')
}

function groupChanges(messages: Record<string, any>): Record<string, unknown> {
  return messages.main.group_changes as Record<string, unknown>
}

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

  it('renders the diff with the shared useFileDiff engine, not a private one', () => {
    const source = readSource(DIALOG)
    expect(source).toContain("from '../composables/useFileDiff'")
    for (const fn of ['buildDiffRows', 'collapseCommonRows', 'toUnifiedRows', 'splitTextLines']) {
      expect(source, `${fn} should come from useFileDiff`).toContain(fn)
    }
    // 0325판 서버 사이드 hunk 파서의 잔재가 남아 있으면 안 된다.
    expect(source).not.toContain('GroupDiffHunk')
    expect(source).not.toContain('GroupDiffLine')
    expect(source).not.toMatch(/\bsplitRows\s*\(/)
  })

  it('never reads the 0325-only diff fields off the response', () => {
    const source = readSource(DIALOG)
    // 이 필드들이 응답에서 사라졌기 때문에, 참조가 남으면 런타임에 undefined를 읽는다.
    for (const dead of ['diff.hunks', 'diff.oversized', 'diff.untracked', 'diff.insertions', 'diff.deletions']) {
      expect(source, `${dead} no longer exists on the response`).not.toContain(dead)
    }
    // +/− 는 이미 props로 들어와 있는 changes 행에서 읽는다 (서버 왕복 없음).
    expect(source).toContain('selectedChange')
    // binary/truncated 는 old/new 각 면에 붙는다 — 응답 최상위가 아니다.
    expect(source).toContain('diff.value?.new.truncated')
  })

  it('types GroupFileDiffData as two content sides', () => {
    const source = readSource(EXPLORER_STORE)
    expect(source).toContain('export interface GroupDiffSide')
    for (const field of ['exists', 'binary', 'truncated', 'content']) {
      expect(source).toContain(field)
    }
    expect(source).toMatch(/interface GroupFileDiffData[\s\S]*?\bstatus:\s*'M'\s*\|\s*'A'\s*\|\s*'D'/)
    expect(source).toMatch(/interface GroupFileDiffData[\s\S]*?\bold:\s*GroupDiffSide/)
    expect(source).toMatch(/interface GroupFileDiffData[\s\S]*?\bnew:\s*GroupDiffSide/)
    // 삭제한 타입들이 되살아나면 계약이 다시 두 벌이 된다.
    expect(source).not.toContain('export interface GroupDiffHunk')
    expect(source).not.toContain('export interface GroupDiffLine')
  })

  it.each(SHARED_DIFF_ENGINE_SPECS)('shared diff engine spec %s still exists', (relative) => {
    expect(existsSync(resolve(CLIENT_DIR, relative))).toBe(true)
  })
})
