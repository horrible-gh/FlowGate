/**
 * task 8: a confirm dialog of the Git panel never has two buttons with
 * the same name. The left button is the shared `gui.git.confirm.cancel` ([돌아가기]); the right one is named by the card
 * that opened the dialog ([병합 중단], [강제 정리], …). The first test reads every `confirmLabel` the Git part passes and
 * fails as soon as one equals the left button (or a new, unaudited way of passing a label appears).
 */
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { G, P, createFakeGitSource, row, status } from './fakeGitSource'

registerAppMessages(i18n)
const GIT = resolve(__dirname, '../../../src/features/git')
const LOCALES = ['ko', 'en', 'ja'] as const
const t = i18n.global.t as unknown as (key: string) => string
const te = i18n.global.te as unknown as (key: string) => boolean

/** Every `confirmLabel:` the part writes: literal `t('key')` keys, and anything else (which must be audited here). */
function collect(): { keys: Set<string>; other: string[] } {
  const keys = new Set<string>()
  const other: string[] = []
  for (const name of readdirSync(GIT).filter((file) => /\.(vue|ts)$/.test(file))) {
    const source = readFileSync(join(GIT, name), 'utf8')
    for (const match of source.matchAll(/confirmLabel:\s*([^,\n]+)/g)) {
      const expression = match[1].trim()
      const literal = /^t\('([^']+)'\)$/.exec(expression)
      if (literal) keys.add(literal[1])
      else other.push(`${name}: ${expression}`)
    }
  }
  return { keys, other }
}

describe('every Git confirm dialog: two different button names — ko / en / ja', () => {
  // GitApplyAccordion picks its label from two keys (apply.merge / apply.merge_push); gitSession.ts only declares the type.
  const AUDITED_OTHER = ['GitApplyAccordion.vue: label', 'gitSession.ts: string']
  const EXTRA_KEYS = ['gui.git.apply.merge', 'gui.git.apply.merge_push', 'gui.git.confirm.ok']

  it('has no unaudited way of naming the confirm button', () => {
    expect(collect().other.sort()).toEqual(AUDITED_OTHER)
  })

  it('finds the labels it is meant to guard', () => {
    const { keys } = collect()
    for (const key of ['gui.git.conflict.abort', 'gui.git.force.action', 'gui.git.review.approve', 'gui.git.reapprove.action', 'gui.git.worktree.revert']) {
      expect(keys.has(key), key).toBe(true)
    }
  })

  it.each(LOCALES)('no confirm label equals the left button in %s', (locale) => {
    i18n.global.locale.value = locale
    const left = t('gui.git.confirm.cancel')
    expect(left).not.toBe('')
    const labels = [...collect().keys, ...EXTRA_KEYS]
    for (const key of labels) {
      expect(te(key), `${locale}: ${key} is missing`).toBe(true)
      const label = t(key)
      expect(label, `${locale}: ${key}`).not.toBe('')
      expect(label, `${locale}: ${key} reads like the left button "${left}"`).not.toBe(left)
    }
    // the dialog's default right button (confirm.ok) is not the left one either
    expect(t('gui.git.confirm.ok')).not.toBe(left)
  })

  it('the left button is never "취소" / "Cancel" / "取消" (the name the abort used on both sides)', () => {
    for (const [locale, bad] of [['ko', '취소'], ['en', 'Cancel'], ['ja', '取消']] as const) {
      i18n.global.locale.value = locale
      expect(t('gui.git.confirm.cancel')).not.toBe(bad)
      expect(t('gui.git.conflict.abort')).not.toBe(bad)
      expect(t('gui.git.review.abort')).not.toBe(bad)
    }
  })
})

describe('the merge-abort dialog on both cards', () => {
  let wrapper: VueWrapper | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })
  const EXPECTED = {
    ko: { left: '돌아가기', right: '병합 중단' },
    en: { left: 'Back', right: 'Abort merge' },
    ja: { left: '戻る', right: 'マージを中断' },
  } as const
  const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)

  it.each(LOCALES)('conflict card (%s): button, dialog title and its two buttons', async (locale) => {
    i18n.global.locale.value = locale
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    wrapper = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'x' }, source: fake.source }, attachTo: document.body, global: { plugins: [i18n] } })
    await flushPromises()
    expect(q('git-conflict-abort')!.getAttribute('aria-label')).toBe(EXPECTED[locale].right)
    q('git-conflict-abort')!.click()
    await flushPromises()
    const buttons = [...document.body.querySelectorAll('.ui-confirm-actions button')].map((button) => button.textContent!.trim())
    expect(buttons).toEqual([EXPECTED[locale].left, EXPECTED[locale].right])
    expect(buttons[0]).not.toBe(buttons[1])
    expect(document.body.querySelector('.ui-confirm-title, [role="alertdialog"] h2, [role="dialog"] h2')?.textContent?.trim() ?? EXPECTED[locale].right).toBe(EXPECTED[locale].right)
  })

  it.each(LOCALES)('merge-approval card (%s): the same button and dialog', async (locale) => {
    i18n.global.locale.value = locale
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }) })
    wrapper = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'x' }, source: fake.source }, attachTo: document.body, global: { plugins: [i18n] } })
    await flushPromises()
    expect(q('git-review-abort')!.textContent!.trim()).toBe(EXPECTED[locale].right)
    q('git-review-abort')!.click()
    await flushPromises()
    const buttons = [...document.body.querySelectorAll('.ui-confirm-actions button')].map((button) => button.textContent!.trim())
    expect(buttons).toEqual([EXPECTED[locale].left, EXPECTED[locale].right])
  })
})
