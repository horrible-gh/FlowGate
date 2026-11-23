import { afterEach, describe, it, expect } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { createFakeGitSource, G, P } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeStatus, manualMergeView, mergeResults, mergeResultRow } from './fakeWorktreeSource'
registerAppMessages(i18n)
let w: VueWrapper | null = null
afterEach(() => { w?.unmount(); document.body.innerHTML = ''; w = null })
// #51: the collapsed summary line shows how many commits merge and push would carry.
async function setup(locale: string, ahead: number | null, unpushed: number) {
  i18n.global.locale.value = locale
  const results = unpushed > 0
    ? mergeResults({ results: [mergeResultRow({ remote: { measured: true, remoteBranchMissing: false, localAheadCount: unpushed, containsMergeCommit: false } })] })
    : mergeResults()
  const wt = createFakeWorktreeSource({ status: worktreeStatus({ files: [] }), merge: manualMergeView({ aheadOfTarget: ahead, workBaseBehind: 13 }), results })
  const fake = createFakeGitSource({}, { worktree: wt.source })
  w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source, readOnly: false }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
}
const cells = () => w!.findAll('[data-test=git-execution-steps] .st').map(c => c.text().replace(/\s+/g, ' ').trim())
const LABELS: Record<string, [string, string]> = { ko: ['파일', '받아오기'], en: ['Files', 'Update'], ja: ['ファイル', '取り込み'] }
describe('#51 summary line merge/push counts', () => {
  for (const locale of ['ko', 'en', 'ja']) {
    const [files, update] = LABELS[locale]
    it(`${locale}: ahead 1, no files, behind 13 → branch 1, origin`, async () => {
      await setup(locale, 1, 0)
      expect((w!.find('[data-test=git-individual-rows]').element as HTMLElement).style.display).toBe('none')
      expect(cells()).toEqual([`${files} 0/0`, `${update} 13`, 'v0.2 1', 'origin'])
      const n = w!.find('[data-test=git-summary-merge-ahead]')
      expect(n.element.tagName).toBe('B')
      expect(n.text()).toBe('1')
      expect(w!.find('[data-test=git-summary-push-ahead]').exists()).toBe(false)
    })
    it(`${locale}: ahead 0 → no number; ahead null → —`, async () => {
      await setup(locale, 0, 0)
      expect(cells()[2]).toBe('v0.2')
      expect(w!.find('[data-test=git-summary-merge-ahead]').exists()).toBe(false)
      w!.unmount(); document.body.innerHTML = ''
      await setup(locale, null, 0)
      expect(cells()[2]).toBe('v0.2 —')
      expect(w!.find('[data-test=git-summary-merge-ahead]').text()).toBe('—')
    })
    it(`${locale}: unpushed 2 → origin 2 in bold`, async () => {
      await setup(locale, 0, 2)
      expect(cells()[3]).toBe('origin 2')
      const n = w!.find('[data-test=git-summary-push-ahead]')
      expect(n.element.tagName).toBe('B')
      expect(n.text()).toBe('2')
    })
  }
})
