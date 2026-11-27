import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { APP_MESSAGES, registerAppMessages } from '@/app/i18n'
import { denialLines, parseCapabilities, type CapabilityDecision } from '@shared/permissions'
import { grantProject, PROJECT_ACTIONS, wireBlock } from '../../helpers/capabilities'

registerAppMessages(i18n)
const tr = (key: string, params?: Record<string, unknown>) => i18n.global.t(key, params ?? {}) as string

const decisionsOf = (role: string, refused: Record<string, { reason: string; minimum_role?: string; rule?: string }>) => {
  const block = parseCapabilities(wireBlock(PROJECT_ACTIONS, role, refused))!
  return { block, refusals: (actions: string[]) => actions.map((action) => ({ action, decision: block.actions[action]! })) }
}
const VIEWER_REFUSED = Object.fromEntries(
  ['project.document.update', 'project.run', 'project.document.approve', 'project.git.write', 'project.git.merge']
    .map((a) => [a, { reason: 'role_limit', minimum_role: a === 'project.document.approve' || a === 'project.git.merge' ? 'role_reviewer' : 'role_developer' }]),
)

beforeEach(() => { i18n.global.locale.value = 'ko' })

describe('denialLines — one line for buttons refused for the same reason', () => {
  it('a view-only person: four buttons, one reason → one line per minimum role at most', () => {
    const { refusals } = decisionsOf('role_viewer', { ...VIEWER_REFUSED, 'project.document.approve': { reason: 'role_limit', minimum_role: 'role_developer' }, 'project.git.merge': { reason: 'role_limit', minimum_role: 'role_developer' } })
    const lines = denialLines(refusals(['project.document.update', 'project.run', 'project.document.approve', 'project.git.write']), 'role_viewer', tr)
    expect(lines).toHaveLength(1)
    expect(lines[0]).toContain('보기 전용')
    expect(lines[0]).toContain('개발자 이상 필요')
  })

  it('a reviewer: edit and run are refused (role_limit), approve is on — only the refused ones are explained', () => {
    const { refusals } = decisionsOf('role_reviewer', { 'project.document.update': { reason: 'role_limit', minimum_role: 'role_developer' }, 'project.run': { reason: 'role_limit', minimum_role: 'role_developer' } })
    const lines = denialLines(refusals(['project.document.update', 'project.run', 'project.document.approve']), 'role_reviewer', tr)
    expect(lines).toHaveLength(1)
    expect(lines[0]).toContain('리뷰어')
  })

  it('different reasons are different lines: role_limit for run, context_rule for edit after final approval', () => {
    const { refusals } = decisionsOf('role_developer', {
      'project.run': { reason: 'role_limit', minimum_role: 'role_admin' },
      'project.document.update': { reason: 'context_rule', rule: 'document_editable' },
    })
    const lines = denialLines(refusals(['project.run', 'project.document.update']), 'role_developer', tr)
    expect(lines).toHaveLength(2)
    expect(lines[0]).not.toBe(lines[1])
  })

  it('nothing is said when every action is allowed', () => {
    const { refusals } = decisionsOf('role_admin', {})
    expect(denialLines(refusals(['project.run', 'project.git.merge']), 'role_admin', tr)).toEqual([])
  })

  it('not a member / no role has its own sentence, and an unknown reason falls back to a plain one', () => {
    const none = denialLines([{ action: 'project.run', decision: { allowed: false, reason: 'not_member', minimumRole: null, rule: null } }], null, tr)
    expect(none[0]).toContain('멤버')
    const odd: CapabilityDecision = { allowed: false, reason: 'something_new', minimumRole: null, rule: null }
    expect(denialLines([{ action: 'project.run', decision: odd }], 'role_viewer', tr)[0]).toBe(tr('gui.permissions.reason.unknown'))
  })

  it('workspace scope words the workspace, not the project', () => {
    const d: CapabilityDecision = { allowed: false, reason: 'role_limit', minimumRole: null, rule: null }
    expect(denialLines([{ action: 'workspace.project.create', decision: d }], 'member', tr, 'workspace')[0]).toContain('워크스페이스')
  })

  it('works through grantProject too (store fixtures)', () => {
    grantProject('px', 'role_viewer', { 'project.run': { reason: 'role_limit', minimum_role: 'role_developer' } })
  })
})

describe('words in all three languages', () => {
  const REASONS = ['not_member', 'role_limit', 'role_limit_plain', 'context_rule', 'token_scope', 'system_admin_only', 'workspace_disabled', 'project_archived', 'account_inactive', 'unknown']
  const flat = (obj: unknown, prefix = ''): string[] => Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    typeof v === 'object' && v !== null ? flat(v, `${prefix}${k}.`) : [`${prefix}${k}`])

  it('ko / en / ja have exactly the same permission keys, none empty', () => {
    const ko = flat(APP_MESSAGES.ko.permissions).sort()
    expect(flat(APP_MESSAGES.en.permissions).sort()).toEqual(ko)
    expect(flat(APP_MESSAGES.ja.permissions).sort()).toEqual(ko)
    for (const lang of ['ko', 'en', 'ja'] as const) {
      for (const key of ko) {
        const value = key.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown>)[k], APP_MESSAGES[lang].permissions)
        expect(String(value).trim(), `${lang}.${key}`).not.toBe('')
      }
    }
  })

  it('every refusal reason of the server has a sentence in every language, and the sentences differ by language', () => {
    for (const reason of REASONS) {
      const texts = (['ko', 'en', 'ja'] as const).map((lang) => {
        i18n.global.locale.value = lang
        return tr(`gui.permissions.reason.${reason}`, { role: 'R', minimum: 'M' })
      })
      expect(new Set(texts).size, reason).toBe(3)
      for (const text of texts) expect(text).not.toContain('gui.permissions')
    }
  })

  it('the role_limit sentence names both roles in each language', () => {
    for (const lang of ['ko', 'en', 'ja'] as const) {
      i18n.global.locale.value = lang
      const d: CapabilityDecision = { allowed: false, reason: 'role_limit', minimumRole: 'role_developer', rule: null }
      const [line] = denialLines([{ action: 'project.run', decision: d }], 'role_viewer', tr)
      expect(line).toContain(tr('gui.permissions.role.role_viewer'))
      expect(line).toContain(tr('gui.permissions.role.role_developer'))
    }
  })
})
