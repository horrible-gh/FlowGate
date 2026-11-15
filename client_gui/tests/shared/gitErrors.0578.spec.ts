import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { normalizeGitError, resolveGitError } from '@shared/gitErrors'

const messages: Record<string, string> = {
  'main.git_errors.network': 'NETWORK',
  'main.git_errors.generic': 'GENERIC',
  'main.git_errors.git_busy': 'BUSY',
  'main.git_errors.dirty_worktree': 'DIRTY {n}',
  fallback: 'FALLBACK',
}
const t = (key: string, params?: Record<string, unknown>) =>
  (messages[key] ?? key).replace('{n}', String(params?.n ?? ''))

describe('gitErrors 0578 stable contract', () => {
  it('normalizes nested and top-level envelopes without mutating input', () => {
    const input = { error: { code: 'git_busy', params: { flag: true }, details: { files: ['a'] }, message: 'RAW' } }
    const before = JSON.stringify(input)
    expect(normalizeGitError(input)).toMatchObject({ code: 'git_busy', params: { flag: true }, network: false })
    expect(JSON.stringify(input)).toBe(before)
    expect(resolveGitError(input, t)).toBe('BUSY')
  })

  it('uses axios response data before outer fields', () => {
    const input = { code: 'outer', response: { status: 409, data: { error: { code: 'git_busy' } } } }
    expect(normalizeGitError(input)).toMatchObject({ code: 'git_busy', status: 409 })
  })

  it('derives only the allowed dirty count from details', () => {
    const input = { error: { code: 'dirty_worktree', details: { files: ['a', 'b'] }, message: 'STDERR' } }
    expect(resolveGitError(input, t)).toBe('DIRTY 2')
  })

  it('never returns raw message, detail, Error.message, or malformed params', () => {
    for (const input of [
      new Error('RAW ERROR'), { message: 'RAW MESSAGE' }, { detail: 'RAW DETAIL' },
      { error: { code: 'unknown', message: 'RAW', params: { n: { bad: true } } } },
      null, [], 'RAW STRING',
    ]) {
      expect(resolveGitError(input, t, 'fallback')).toBe('FALLBACK')
    }
  })

  it('distinguishes a network failure', () => {
    expect(resolveGitError({ isAxiosError: true, request: {} }, t)).toBe('NETWORK')
  })

  // 0685 T0006 §5: a branch that moved after the merge started is told as such, with the
  // way out, instead of the generic failure.
  it.each(['stale_source', 'stale_target'])('maps %s to its own key', (code) => {
    const input = { response: { status: 409, data: { ok: false, error: { code, message: 'RAW' } } } }
    expect(resolveGitError(input, t, 'fallback')).toBe(`main.git_errors.${code}`)
  })
})

describe('gitErrors 0685 stale branch wording', () => {
  it.each(['ko', 'en', 'ja'] as const)('%s names the moved branch and the abort-and-restart action', (locale) => {
    const tr = (key: string, params?: Record<string, unknown>) => String(i18n.global.t(key, params ?? {}, { locale }))
    const generic = tr('main.git_errors.generic')
    const finalizeFailed = tr('main.git_finalize.failed')
    const words = {
      ko: { source: '원본 브랜치', target: '대상 브랜치', action: '중단하고 다시 시작' },
      en: { source: 'source branch', target: 'target branch', action: 'Abort this merge and start it again' },
      ja: { source: 'ソースブランチ', target: '対象ブランチ', action: '中止して、もう一度開始' },
    }[locale]
    for (const side of ['source', 'target'] as const) {
      const input = { response: { status: 409, data: { error: { code: `stale_${side}` } } } }
      const message = resolveGitError(input, tr, 'main.git_finalize.failed')
      expect(message).not.toBe(generic)
      expect(message).not.toBe(finalizeFailed)
      expect(message).not.toContain('main.git_errors')
      expect(message).toContain(words[side])
      expect(message).toContain(words.action)
    }
  })
})
