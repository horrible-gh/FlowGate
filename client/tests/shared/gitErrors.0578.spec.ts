import { describe, expect, it } from 'vitest'
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
})
