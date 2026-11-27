import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import PermissionNote from '@/components/ui/PermissionNote.vue'
import { registerAppMessages } from '@/app/i18n'
import { grantProject, resetCapabilities } from '../helpers/capabilities'

vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest: () => new Promise(() => undefined),
}))

registerAppMessages(i18n)
const ACTIONS = ['project.document.update', 'project.run', 'project.document.approve', 'project.git.write', 'project.git.merge']
const note = (projectId: string | null) => mount(PermissionNote, { props: { projectId, actions: ACTIONS }, global: { plugins: [i18n] } })
const lines = (w: ReturnType<typeof note>) => w.findAll('[data-test="permission-note-line"]').map((l) => l.text())

beforeEach(() => { i18n.global.locale.value = 'ko'; resetCapabilities() })

describe('PermissionNote — why the buttons are off, one line per reason', () => {
  it('a view-only person: edit / run / Git are refused for one reason → one line, naming the role and the role needed', () => {
    grantProject('p1', 'role_viewer', {
      'project.document.update': { reason: 'role_limit', minimum_role: 'role_developer' },
      'project.run': { reason: 'role_limit', minimum_role: 'role_developer' },
      'project.document.approve': { reason: 'role_limit', minimum_role: 'role_developer' },
      'project.git.write': { reason: 'role_limit', minimum_role: 'role_developer' },
      'project.git.merge': { reason: 'role_limit', minimum_role: 'role_developer' },
    })
    const w = note('p1')
    expect(lines(w)).toHaveLength(1)
    expect(lines(w)[0]).toContain('보기 전용')
    expect(lines(w)[0]).toContain('개발자 이상 필요')
    expect(w.get('[data-test="permission-note"]').attributes('role')).toBe('note')
  })

  it('a reviewer: edit and run are off, approve is on — the line is about edit / run only (one line, the roles needed agree)', () => {
    grantProject('p1', 'role_reviewer', {
      'project.document.update': { reason: 'role_limit', minimum_role: 'role_developer' },
      'project.run': { reason: 'role_limit', minimum_role: 'role_developer' },
      'project.git.write': { reason: 'role_limit', minimum_role: 'role_developer' },
    })
    const w = note('p1')
    expect(lines(w)).toHaveLength(1)
    expect(lines(w)[0]).toContain('리뷰어')
  })

  it('different reasons are different lines: run needs a higher role, edit is locked by a context rule', () => {
    grantProject('p1', 'role_developer', {
      'project.run': { reason: 'role_limit', minimum_role: 'role_admin' },
      'project.document.update': { reason: 'context_rule', rule: 'document_editable' },
    })
    expect(lines(note('p1'))).toHaveLength(2)
  })

  it('shows nothing for an administrator (everything allowed), with no project, or while no answer is in', () => {
    grantProject('p1', 'role_admin')
    expect(note('p1').find('[data-test="permission-note"]').exists()).toBe(false)
    expect(note(null).find('[data-test="permission-note"]').exists()).toBe(false)
    expect(note('p-unknown').find('[data-test="permission-note"]').exists()).toBe(false)
  })

  it.each([['en', 'View only'], ['ja', '閲覧のみ']] as const)('speaks %s', (lang, roleWord) => {
    i18n.global.locale.value = lang
    grantProject('p1', 'role_viewer', { 'project.run': { reason: 'role_limit', minimum_role: 'role_developer' } })
    const [line] = lines(note('p1'))
    expect(line).toContain(roleWord)
    expect(line).not.toContain('gui.permissions')
  })
})

describe('PermissionNote with the answer that came with one document', () => {
  it('uses the document\'s block (context rules included) instead of the project\'s', async () => {
    grantProject('p1', 'role_developer') // the project says yes ...
    const { parseCapabilities } = await import('@shared/permissions/capabilities')
    const { wireBlock } = await import('../helpers/capabilities')
    const block = parseCapabilities(wireBlock(['project.document.update'], 'role_developer', {
      'project.document.update': { reason: 'context_rule', rule: 'document_editable' },
    }))
    const w = mount(PermissionNote, { props: { projectId: 'p1', actions: ['project.document.update'], block }, global: { plugins: [i18n] } })
    expect(w.findAll('[data-test="permission-note-line"]').map((l) => l.text())).toEqual([expect.stringContaining('고칠 수 없는')]) // ... the document says no
  })
})
