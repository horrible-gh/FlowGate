import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import Select from '@/components/ui/Select.vue'
import AiRoleMatrix from '@/features/ai/groups/AiRoleMatrix.vue'
import AiRoleSelect from '@/features/ai/roles/AiRoleSelect.vue'
import * as api from '@/features/ai/api'
import type { AiRoleProfiles } from '@/features/ai/types'

// v02gui.0031.0011-T (D): the AI model pickers opt into `appearance: base-select`; nothing else does.
// jsdom has no layout/pseudo-element engine, so computed styles are NOT asserted here: the CSS text and the class wiring are.
registerAppMessages(i18n)
const read = (rel: string) => readFileSync(resolve(__dirname, '../../../src', rel), 'utf8')
const css = read('features/ai/aiBaseSelect.css')

describe('0031 D: aiBaseSelect.css', () => {
  it('is wrapped in @supports so browsers without base-select keep today\'s look', () => {
    const outside = css.replace(/\/\*[\s\S]*?\*\//g, '').trim()
    expect(outside.startsWith('@supports (appearance: base-select) {')).toBe(true)
    expect(outside.endsWith('}')).toBe(true)
    expect(css).toContain('::picker(select)')
  })
  it('makes the scrollbar thick and contrasted with existing tokens (no thin, no new global tokens)', () => {
    expect(css).toContain('scrollbar-width: auto;')
    expect(css).not.toMatch(/scrollbar-width:\s*thin/)
    expect(css).toContain('scrollbar-color: var(--fg2-muted) var(--fg2-bg);')
    expect(css).toContain('background: var(--fg2-panel);')
    expect(css).not.toMatch(/--fg2-[\w-]+\s*:/)
  })
  it('styles only selects that carry the opt-in class', () => {
    const body = css.replace(/\/\*[\s\S]*?\*\//g, '')
    for (const rule of body.match(/[^{}]+(?=\{)/g) ?? []) {
      if (rule.trim().startsWith('@supports')) continue
      for (const part of rule.split(',')) expect(part.trim()).toMatch(/^select\.ai-base-select/)
    }
  })
  it('leaves the global theme and the shared Select.vue untouched', () => {
    expect(read('app/styles/theme.css')).not.toContain('base-select')
    expect(read('components/ui/Select.vue')).not.toContain('base-select')
    expect(mount(Select, { props: { modelValue: 'a', options: [{ value: 'a', label: 'A' }] } }).get('select').classes()).not.toContain('ai-base-select')
  })
})

describe('0031 D: class wiring and unchanged behaviour', () => {
  beforeEach(() => { vi.restoreAllMocks(); i18n.global.locale.value = 'ko'; setCurrentUser({ username: 'admin', email: null, isAdmin: true }) })
  afterEach(() => { setCurrentUser(null); document.body.innerHTML = '' })

  it('AiRoleSelect renders a native select with the class and still emits the chosen AI', async () => {
    const wrapper = mount(AiRoleSelect, { props: { modelValue: null, providers: [{ id: 'a', name: 'Model A' }, { id: 'b', name: 'Model B' }], label: 'role' }, global: { plugins: [i18n] } })
    const select = wrapper.get('select')
    expect(select.classes()).toEqual(expect.arrayContaining(['ui-select', 'ai-base-select']))
    expect(select.findAll('option').map(option => option.text())).toContain('Model B')
    await select.setValue('assigned:b')
    expect(wrapper.emitted('update:modelValue')).toEqual([[{ kind: 'assigned', providerId: 'b' }]])
    wrapper.unmount()
  })

  it('AiRoleMatrix model and review-count selects carry the class, the add-kind select does not, and a pick still saves', async () => {
    const saved: AiRoleProfiles = { ai_group_id: 'g', items: [{ type_key: 'common', position: 0, roles: { instruction: { kind: 'unset' }, execution: { kind: 'unset' }, review: { kind: 'unset' } } }] }
    vi.spyOn(api, 'getAiRoleProfiles').mockResolvedValue(JSON.parse(JSON.stringify(saved)))
    vi.spyOn(api, 'getAiRoleProfileOptions').mockResolvedValue({ types: [], providers: [{ provider_id: 'p1', provider_name: 'Model One' }] })
    const set = vi.spyOn(api, 'setAiRoleProfileCell').mockResolvedValue(JSON.parse(JSON.stringify(saved)))
    const wrapper = mount(AiRoleMatrix, { props: { groupId: 'g', canEdit: true }, global: { plugins: [i18n] } })
    await flushPromises()
    const cells = wrapper.findAll('select.role-select')
    // Three AI cells plus the review-count cell (0027): one row, one look.
    expect(cells).toHaveLength(4)
    expect(cells.map(cell => cell.attributes('data-role'))).toEqual(['instruction', 'execution', 'review', 'review_count'])
    cells.forEach(cell => expect(cell.classes()).toContain('ai-base-select'))
    expect(wrapper.get('select.add-kind-select').classes()).not.toContain('ai-base-select')
    await wrapper.get('[data-role="execution"]').setValue('provider:p1')
    await flushPromises()
    expect(set).toHaveBeenCalledWith('g', 'common', 'execution', { kind: 'assigned', provider_id: 'p1' })
    wrapper.unmount()
  })
})
