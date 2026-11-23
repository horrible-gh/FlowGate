import { flushPromises } from '@vue/test-utils'

// flowgate.v02test.0068 (v16 mockup): the Git tab keeps one window entry, the footer `Diff`
// button. The other window tabs (파일 관리 / Base 반영 / 상태) are reached from that window's tabs.
export async function openGitWindowTab(tab: string): Promise<void> {
  const diff = document.body.querySelector<HTMLElement>('[data-test="git-worktree-entry"][data-tab="diff"]')
  if (!diff) throw new Error('missing the Diff entry')
  diff.click()
  await flushPromises()
  if (tab === 'diff') return
  const win = document.body.querySelector<HTMLElement>(`[data-test="git-window-tab"][data-tab="${tab}"]`)
  if (!win) throw new Error(`missing window tab ${tab}`)
  win.click()
  await flushPromises()
}
