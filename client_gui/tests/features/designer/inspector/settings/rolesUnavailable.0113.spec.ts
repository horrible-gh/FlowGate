/** flowgate.v02.0113 T#1 (0021-T): P-1 card_roles_unavailable → the roles_unavailable kind on the check / requirement cards. */
import { describe, expect, it } from 'vitest'
import { checkSettingsErrorKey, checkSettingsView, type CheckSettingsFailureKind } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import { requirementSettingsErrorKey, requirementSettingsView, type RequirementSettingsFailureKind } from '@/features/designer/inspector/features/requirementSettings/requirementSettingsModel'
import checkMessages from '@/features/designer/inspector/features/checkSettings/messages'
import requirementMessages from '@/features/designer/inspector/features/requirementSettings/messages'
import type { SettingsNodeErrorKind } from '@/features/document'

const KINDS: readonly SettingsNodeErrorKind[] = ['roles_unavailable', 'locked', 'disposed', 'unavailable', 'invalid', 'forbidden', 'not_found', 'network', 'other']
const LOCALES = ['ko', 'en', 'ja'] as const
const text = (block: unknown, key: string) => (block as Record<string, string>)[key.split('.')[1]]

describe('0113 T#1 roles_unavailable', () => {
  it('every settings node error kind is a check / requirement failure kind', () => {
    const check: readonly CheckSettingsFailureKind[] = KINDS
    const requirement: readonly RequirementSettingsFailureKind[] = KINDS
    expect(check).toHaveLength(KINDS.length)
    expect(requirement).toHaveLength(KINDS.length)
  })

  it('reads roles_unavailable with its own sentence, never the generic one', () => {
    expect(checkSettingsErrorKey('roles_unavailable')).toBe('error.roles_unavailable')
    expect(requirementSettingsErrorKey('roles_unavailable')).toBe('error.roles_unavailable')
    expect(checkSettingsErrorKey('other')).toBe('error.other')
    expect(requirementSettingsErrorKey('unavailable')).toBe('error.unavailable')
  })

  it('a refused write shows failure.roles_unavailable', () => {
    const check = checkSettingsView({ card: true, node: null, pending: {}, branches: [], readOnly: null, placing: false, disposed: false, failure: { field: 'merge_mode', kind: 'roles_unavailable' } })
    expect(check.failures.merge_mode).toBe('failure.roles_unavailable')
    const requirement = requirementSettingsView({ card: true, node: null, pending: {}, teams: [], readOnly: null, placing: false, disposed: false, failure: { field: 'team', kind: 'roles_unavailable' } })
    expect(requirement.failures.team).toBe('failure.roles_unavailable')
  })

  it('has ko/en/ja sentences for every kind', () => {
    for (const locale of LOCALES) {
      for (const messages of [checkMessages, requirementMessages]) {
        for (const kind of KINDS) {
          expect(text(messages[locale].failure, `failure.${kind}`)).toBeTruthy()
          const key = messages === checkMessages ? checkSettingsErrorKey(kind) : requirementSettingsErrorKey(kind)
          expect(text(messages[locale].error, key)).toBeTruthy()
        }
      }
    }
  })
})
