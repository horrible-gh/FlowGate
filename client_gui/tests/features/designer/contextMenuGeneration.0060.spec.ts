import { afterEach, expect, it } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import DesignerContextMenu from '@/features/designer/contextMenu/DesignerContextMenu.vue'
import { createDesignerMenuHost } from '@/features/designer/contextMenu/host'
import { modelFor } from './contextMenuFixtures.0060'

let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
it('rejects a click from a previous rendered generation while a new target awaits rendering (0060)', async () => {
  const host = createDesignerMenuHost()
  wrapper = mount(DesignerContextMenu, { props: { host }, attachTo: document.body,
    global: { plugins: [createI18n({ legacy: false, locale: 'ko' })] } })
  host.open(modelFor({ kind: 'tab', flowId: 'old', tabIds: ['old', 'new'] }, undefined, { openTabIds: ['old', 'new'] }), { x: 1, y: 1 })
  await flushPromises()
  const oldButton = document.querySelector<HTMLElement>('[data-key="tab.close"]')!
  host.open(modelFor({ kind: 'tab', flowId: 'new', tabIds: ['old', 'new'] }, undefined, { openTabIds: ['old', 'new'] }), { x: 2, y: 2 })
  oldButton.click()
  expect(wrapper.emitted('request')).toBeUndefined()
  expect(host.current.value?.model.target).toMatchObject({ flowId: 'new' })
  await flushPromises()
  oldButton.click() // detached DOM from an earlier generation stays inert as well
  expect(wrapper.emitted('request')).toBeUndefined()
  document.querySelector<HTMLElement>('[data-key="tab.close"]')!.click()
  expect(wrapper.emitted('request')).toEqual([[{ command: 'tab.close', target: { kind: 'tab', flowId: 'new', tabIds: ['old', 'new'] } }]])
})
