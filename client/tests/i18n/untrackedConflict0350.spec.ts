/**
 * flowgate.default.0350 T0004 §2.3 / TR0005 §3.5 — locale coverage for the
 * `base_untracked_conflict` recovery UI.
 *
 * `tests/i18n/locales.spec.ts` already asserts whole-file ko/en/ja parity, but
 * that file is RED on this branch for an unrelated reason (18 missing
 * `main.git_finalize.archive.*` keys owned by another group), so its verdict
 * cannot be used as evidence that *these* keys landed in all three locales.
 * This spec pins the 0350 keys on their own so the TS has a green oracle that
 * survives the neighbouring failure.
 */
import en from '../../shared/i18n/en'
import ja from '../../shared/i18n/ja'
import ko from '../../shared/i18n/ko'

type Messages = Record<string, unknown>

/** The exact keys TR0005 added, grouped by the namespace that owns them. */
const FINALIZE_KEYS = [
  'untracked_conflict_dialog_title',
  'untracked_conflict_dialog_body',
  'untracked_conflict_commit',
  'untracked_conflict_remove',
  'untracked_conflict_remove_note',
  'untracked_conflict_still',
] as const

const STATUS_KEYS = [
  'base_untracked_conflict_pending',
  'base_untracked_remove_btn',
  'base_untracked_remove_confirm',
  'base_untracked_remove_done',
] as const

const LOCALES: Array<[string, Messages]> = [
  ['ko', ko as Messages],
  ['en', en as Messages],
  ['ja', ja as Messages],
]

function at(messages: Messages, path: string): unknown {
  return path.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object' ? (node as Messages)[part] : undefined,
    messages,
  )
}

function placeholders(message: string): string[] {
  return [...message.matchAll(/\{([^}]+)\}/g)].map((match) => match[1]).sort()
}

const PATHS = [
  ...FINALIZE_KEYS.map((key) => `main.git_finalize.${key}`),
  ...STATUS_KEYS.map((key) => `main.git_status.${key}`),
]

describe('i18n — base_untracked_conflict recovery keys (0350)', () => {
  it.each(LOCALES)('%s defines every 0350 key as a non-empty string', (_, messages) => {
    const missing = PATHS.filter((path) => {
      const value = at(messages, path)
      return typeof value !== 'string' || value.trim() === ''
    })
    expect(missing).toEqual([])
  })

  it.each(LOCALES)('%s keeps the ko interpolation placeholders', (_, messages) => {
    for (const path of PATHS) {
      expect(placeholders(String(at(messages, path) ?? '')), path).toEqual(
        placeholders(String(at(ko as Messages, path) ?? '')),
      )
    }
  })

  it.each([
    ['en', en as Messages],
    ['ja', ja as Messages],
  ])('%s does not leak Korean syllables into the 0350 keys', (_, messages) => {
    const leaked = PATHS.filter((path) => /[가-힣]/.test(String(at(messages, path) ?? '')))
    expect(leaked).toEqual([])
  })

  it('names the destructive branch distinctly from the commit branch', () => {
    // The T's completion bar: the delete entry point must not be folded into
    // the commit wording. Different key, different copy, in every locale.
    for (const [, messages] of LOCALES) {
      const commit = String(at(messages, 'main.git_finalize.untracked_conflict_commit'))
      const remove = String(at(messages, 'main.git_finalize.untracked_conflict_remove'))
      expect(remove).not.toEqual(commit)
    }
    // ...and the delete confirmation must state that it is unrecoverable.
    expect(
      String(at(ko as Messages, 'main.git_status.base_untracked_remove_confirm')),
    ).toContain('되돌릴 수 없습니다')
  })
})
