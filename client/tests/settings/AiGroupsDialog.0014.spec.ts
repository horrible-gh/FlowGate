// flowgate.v02.0014 R0001 HOLD 2.1 + 2.5: the AI Group edit modal runs on the common dialog
// layer (DialogShell/DialogHeader/DialogFooter + useDialogStack) and every relative import of
// the 0014 client files resolves. The old modal was a hand-made overlay with a relative import
// one directory too deep, which `vite build` rejected.
import { readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import AiGroupsPanel from '../../src/settings/components/AiGroupsPanel.vue'

const { getRequest, postRequest, putRequest, deleteRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest, deleteRequest }))

const ROOT = process.cwd()
const CHANGED_0014_FILES = [
  'src/settings/components/AiGroupsPanel.vue',
  'src/settings/components/AiEvaluationPanel.vue',
  'src/settings/views/project/AiProjectSettingsView.vue',
]
const isFile = (path: string) => { try { return statSync(path).isFile() } catch { return false } }
const read = (path: string) => readFileSync(join(ROOT, path), 'utf8')

const wrappers: VueWrapper[] = []
afterEach(() => {
  while (wrappers.length) wrappers.pop()?.unmount()
  vi.clearAllMocks()
  resetDialogSystem()
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

async function mountPanel() {
  getRequest.mockResolvedValue({ data: { items: [] } })
  const wrapper = mount(AiGroupsPanel, {
    props: { projectId: 'project', providers: [{ id: 'p1', name: 'One' }] },
    global: { plugins: [i18n] },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
async function openCreate(wrapper: VueWrapper) {
  await wrapper.find('.card-hd button').trigger('click')
  await flushPromises()
}
const surface = () => document.body.querySelector<HTMLElement>('.fg-dialog-surface')
const saveButton = () => document.body.querySelector<HTMLButtonElement>('[data-dialog-action-id="save"]')!
const escape = () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))

describe('AiGroupsPanel imports (HOLD 2.1)', () => {
  it.each(CHANGED_0014_FILES)('%s: every relative import resolves to a file', (file) => {
    const source = read(file)
    const specs = [...source.matchAll(/(?:from|import)\s+['"](\.{1,2}\/[^'"]+)['"]/g)].map((m) => m[1])
    for (const spec of specs) {
      const base = resolve(ROOT, dirname(file), spec)
      const candidates = [base, `${base}.ts`, `${base}.js`, `${base}.vue`, join(base, 'index.ts'), join(base, 'index.js')]
      expect(candidates.some(isFile), `${file}: '${spec}' does not resolve`).toBe(true)
    }
  })

  it.each(CHANGED_0014_FILES)('%s: a TypeScript script block does not use the @main alias (not in tsconfig.app.json paths, so vue-tsc rejects it)', (file) => {
    const typed = /<script[^>]*\blang=["']ts["'][^>]*>([\s\S]*?)<\/script>/.exec(read(file))
    if (typed) expect(typed[1]).not.toMatch(/from\s+['"]@main\//)
  })

  it('the i18n keys used by the AI Group panel and settings tabs exist in every locale', () => {
    for (const locale of ['ko', 'en', 'ja']) {
      for (const key of ['settings.project.ai_v02.groups', 'settings.project.ai_v02.provider', 'settings.project.ai_v02.tabs_label']) {
        expect(i18n.global.te(key, locale), `${locale}: ${key}`).toBe(true)
      }
    }
  })

  it('AiGroupsPanel reaches useDialogStack through the correct ../../main path', () => {
    const source = read('src/settings/components/AiGroupsPanel.vue')
    expect(source).not.toContain('../../../main')
    expect(source).toContain("from '../../main/composables/useDialogStack'")
  })
})

describe('AI Group modal is the common dialog (HOLD 2.5)', () => {
  it('is built on DialogShell + DialogHeader + DialogFooter and has no hand-made overlay', () => {
    const source = read('src/settings/components/AiGroupsPanel.vue')
    for (const tag of ['<DialogShell', '<DialogHeader', '<DialogFooter']) expect(source).toContain(tag)
    expect(source).not.toMatch(/class="[^"]*\bai-group-(backdrop|modal)\b/)
    expect(source).not.toContain('role="dialog"')
  })

  it('opens as a teleported form dialog with [cancel][save] in role order and an autofocus name field', async () => {
    const wrapper = await mountPanel()
    expect(surface()).toBeNull()
    await openCreate(wrapper)
    const dialog = surface()!
    expect(dialog.getAttribute('role')).toBe('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(dialog.textContent).toContain(i18n.global.t('settings.project.ai_v02.group_form'))
    expect([...dialog.querySelectorAll('[data-dialog-action-role]')].map((b) => b.getAttribute('data-dialog-action-role')))
      .toEqual(['cancel', 'primary'])
    expect(dialog.querySelector('input[data-dialog-autofocus]')).not.toBeNull()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('locks body scroll while open and releases it on close', async () => {
    const wrapper = await mountPanel()
    await openCreate(wrapper)
    expect(document.body.style.overflow).toBe('hidden')
    escape()
    await flushPromises()
    expect(surface()).toBeNull()
    expect(document.body.style.overflow).not.toBe('hidden')
  })

  it('ESC and the cancel action close it; a backdrop press does not (form variant)', async () => {
    const wrapper = await mountPanel()
    await openCreate(wrapper)
    const overlay = document.body.querySelector('.fg-dialog-overlay')!
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await flushPromises()
    expect(surface()).not.toBeNull()
    escape()
    await flushPromises()
    expect(surface()).toBeNull()

    await openCreate(wrapper)
    document.body.querySelector<HTMLButtonElement>('[data-dialog-action-id="cancel"]')!.click()
    await flushPromises()
    expect(surface()).toBeNull()
  })

  it('saves through the footer action and closes', async () => {
    const wrapper = await mountPanel()
    postRequest.mockResolvedValue({ data: {} })
    await openCreate(wrapper)
    const name = surface()!.querySelector('input') as HTMLInputElement
    name.value = 'Team'
    name.dispatchEvent(new Event('input'))
    await flushPromises()
    saveButton().click()
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects/project/ai-groups',
      expect.objectContaining({ name: 'Team', work_executor_provider_id: 'p1' }))
    expect(surface()).toBeNull()
  })

  it('keeps the dialog open and shows the error when save fails', async () => {
    const wrapper = await mountPanel()
    postRequest.mockRejectedValue({ response: { data: { error: { code: 'ai_group_name_taken' } } } })
    await openCreate(wrapper)
    saveButton().click()
    await flushPromises()
    expect(surface()).not.toBeNull()
    expect(surface()!.querySelector('[role="alert"]')?.textContent).toContain('ai_group_name_taken')
  })

  it('disables save while a request is in flight', async () => {
    const wrapper = await mountPanel()
    let release!: (v: unknown) => void
    postRequest.mockReturnValue(new Promise((resolveRequest) => { release = resolveRequest }))
    await openCreate(wrapper)
    saveButton().click()
    await flushPromises()
    expect(saveButton().disabled).toBe(true)
    release({ data: {} })
    await flushPromises()
    expect(surface()).toBeNull()
  })
})

describe('AI Group dialog close policy (review E-C02/E-C03)', () => {
  const headerClose = () => document.body.querySelector<HTMLButtonElement>('.fg-dialog-header__close')
  const cancelButton = () => document.body.querySelector<HTMLButtonElement>('[data-dialog-action-id="cancel"]')!
  const pendingSave = () => {
    let release!: (v: unknown) => void
    postRequest.mockReturnValue(new Promise((resolveRequest) => { release = resolveRequest }))
    return () => release({ data: {} })
  }

  it('every dismissal path goes through requestClose: the header X is not wired straight to closeModal', () => {
    const source = read('src/settings/components/AiGroupsPanel.vue')
    expect(source).toContain('@close="requestClose"')
    expect(source).toContain('@request-close="requestClose"')
    expect(source).not.toMatch(/@(close|request-close)="closeModal"/)
    expect(source).toContain(':closeable="!saving"')
  })

  it('the header X closes an idle dialog', async () => {
    const wrapper = await mountPanel()
    await openCreate(wrapper)
    expect(headerClose()).not.toBeNull()
    headerClose()!.click()
    await flushPromises()
    expect(surface()).toBeNull()
  })

  it('while a save is in flight the header X is gone and ESC / cancel cannot dismiss; success closes it', async () => {
    const wrapper = await mountPanel()
    const release = pendingSave()
    await openCreate(wrapper)
    saveButton().click()
    await flushPromises()
    expect(headerClose()).toBeNull()
    expect(cancelButton().disabled).toBe(true)
    escape()
    cancelButton().click()
    await flushPromises()
    expect(surface()).not.toBeNull()
    release()
    await flushPromises()
    expect(surface()).toBeNull()
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('a failed save gives the header X back and keeps the dialog open', async () => {
    const wrapper = await mountPanel()
    let fail!: (e: unknown) => void
    postRequest.mockReturnValue(new Promise((_resolve, reject) => { fail = reject }))
    await openCreate(wrapper)
    saveButton().click()
    await flushPromises()
    expect(headerClose()).toBeNull()
    fail({ response: { data: { error: { code: 'ai_group_name_taken' } } } })
    await flushPromises()
    expect(surface()).not.toBeNull()
    expect(headerClose()).not.toBeNull()
    expect(saveButton().disabled).toBe(false)
  })

  it('a save that finishes after the editor was replaced (project A -> B -> A) does not close or lock the new editor', async () => {
    const wrapper = await mountPanel()
    const release = pendingSave()
    await openCreate(wrapper)
    saveButton().click()
    await flushPromises()
    await wrapper.setProps({ projectId: 'other' })
    await flushPromises()
    await wrapper.setProps({ projectId: 'project' })
    await flushPromises()
    expect(surface()).toBeNull()

    await openCreate(wrapper)
    expect(surface()).not.toBeNull()
    expect(saveButton().disabled).toBe(false)
    release()
    await flushPromises()
    expect(surface()).not.toBeNull()
    expect(saveButton().disabled).toBe(false)
    expect(headerClose()).not.toBeNull()
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('a stale failed save does not write its error into the new editor', async () => {
    const wrapper = await mountPanel()
    let fail!: (e: unknown) => void
    postRequest.mockReturnValue(new Promise((_resolve, reject) => { fail = reject }))
    await openCreate(wrapper)
    saveButton().click()
    await flushPromises()
    await wrapper.setProps({ projectId: 'other' })
    await wrapper.setProps({ projectId: 'project' })
    await flushPromises()
    await openCreate(wrapper)
    fail({ response: { data: { error: { code: 'stale_error' } } } })
    await flushPromises()
    expect(surface()).not.toBeNull()
    expect(surface()!.querySelector('[role="alert"]')).toBeNull()
  })
})
