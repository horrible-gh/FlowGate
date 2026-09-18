/** flowgate.default.0560 T0026 — shared git file-list/diff rules have one owner. */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const CLIENT_DIR = resolve(__dirname, '../..')
const component = (name: string) =>
  readFileSync(resolve(CLIENT_DIR, 'src/main/components/' + name), 'utf8')
const shared = readFileSync(resolve(CLIENT_DIR, 'src/main/components/dialogs/gitDiffFileList.css'), 'utf8')

const TARGET_SELECTORS = [
  '.gcd-mono', '.gcd-dot', '.gcd-retry', '.gcd-bd', '.gcd-filelist', '.gcd-nomatch',
  '.gcd-file', '.gcd-file:hover', '.gcd-file.active', '.gcd-file-top', '.gcd-file-name',
  '.gcd-file-dir', '.gcd-badge', '.gcd-badge-added', '.gcd-badge-modified',
  '.gcd-badge-deleted', '.gcd-diffwrap', '.gcd-diff-hd', '.gcd-diff-path',
  '.gcd-diff-state', '.gcd-diff-error', '.gcd-diff', '.gcd-gap', '.gcd-line',
  '.gcd-ln', '.gcd-sign', '.gcd-text', '.gcd-line-add', '.gcd-text.gcd-line-add',
  '.gcd-line-del', '.gcd-text.gcd-line-del',
] as const

function selectors(css: string): string[] {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '')
  return [...withoutComments.matchAll(/([^{}]+)\{/g)]
    .flatMap((match) => match[1].split(','))
    .map((selector) => selector.trim())
}

const definitionCount = (css: string, selector: string) =>
  selectors(css).filter((candidate) => candidate === selector).length

describe('T0026 — git diff CSS has a single shared owner', () => {
  const group = component('GroupChangesDialog.vue')
  const review = component('GitMergeReviewDialog.vue')

  it('both scoped blocks import the same partial exactly once', () => {
    for (const source of [group, review]) {
      expect(source.match(/@import '\.\/dialogs\/gitDiffFileList\.css';/g)).toHaveLength(1)
    }
  })

  it('defines every shared selector only in the partial', () => {
    for (const selector of TARGET_SELECTORS) {
      expect(definitionCount(shared, selector), selector).toBe(1)
      expect(definitionCount(group, selector), selector + ' remained in GroupChangesDialog').toBe(0)
      expect(definitionCount(review, selector), selector + ' remained in GitMergeReviewDialog').toBe(0)
    }
  })

  it('leaves GroupChangesDialog-only rules scoped in their component', () => {
    for (const selector of [
      '.gcd-file-stats', '.gcd-file-nostat', '.gcd-bar', '.gcd-diff-lines',
      '.gcd-diff-nav', '.gcd-notice', '.gcd-srow', '.gcd-line-changed',
      '.gcd-line-blank', '.gcd-seg', '.gcd-artifacts',
    ]) {
      expect(definitionCount(group, selector), selector).toBeGreaterThan(0)
      expect(definitionCount(shared, selector), selector + ' leaked into the partial').toBe(0)
    }
  })
})
