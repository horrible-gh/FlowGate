import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'

import DocumentEditDialog from '@main/components/DocumentEditDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const BODY = 'one\ntwo'
const FULL = '---\ntitle: Demo\n---\none\ntwo'

function mountDialog() {
  return mount(DocumentEditDialog, {
    props: {
      visible: true,
      tab: {
        id: 'flowgate.default.0590.0004-T2',
        title: 'Demo',
        type: 'document',
        typeCode: 'T2',
        projectId: 'flowgate',
      } as any,
      body: BODY,
      fullContent: FULL,
      loadedBody: BODY,
      loadedFullContent: FULL,
      headerVisible: false,
      loading: false,
      saving: false,
      loadError: '',
      saveError: '',
    },
    global: {
      plugins: [i18n],
      stubs: { teleport: true, AppIcon: true },
    },
  })
}

function gutterLast(wrapper: ReturnType<typeof mountDialog>): string {
  const text = wrapper.get('.document-editor__textarea .line-numbered-textarea__gutter').element.textContent ?? ''
  return text.split('\n').at(-1) ?? ''
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
})

afterEach(() => {
  resetDialogSystem()
})

describe('DocumentEditDialog line numbers (0590)', () => {
  it('shows line numbers in body-only mode and preserves update/dirty contracts', async () => {
    const wrapper = mountDialog()
    const textarea = wrapper.get<HTMLTextAreaElement>('.document-editor__textarea textarea')

    expect(gutterLast(wrapper)).toBe('2')
    expect(textarea.attributes()).toHaveProperty('data-dialog-autofocus')
    expect(textarea.attributes('wrap')).toBe('off')

    await textarea.setValue('one\ntwo\nthree')
    expect(wrapper.emitted('update:body')?.at(-1)).toEqual(['one\ntwo\nthree'])

    await wrapper.setProps({ body: 'one\ntwo\nthree' })
    expect(gutterLast(wrapper)).toBe('3')
    expect((wrapper.vm as any).isDirty).toBe(true)

    wrapper.unmount()
  })

  it('uses the same line-numbered editor for full-content mode', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ headerVisible: true })

    expect(gutterLast(wrapper)).toBe('5')
    const textarea = wrapper.get<HTMLTextAreaElement>('.document-editor__textarea textarea')
    await textarea.setValue(`${FULL}\nextra`)
    expect(wrapper.emitted('update:fullContent')?.at(-1)).toEqual([`${FULL}\nextra`])

    wrapper.unmount()
  })
})
