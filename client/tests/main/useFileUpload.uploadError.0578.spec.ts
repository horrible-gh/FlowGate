import { defineComponent, h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFileUpload } from '@main/composables/useFileUpload'

// flowgate.default.0578 T0010 §2.5/작업4-3: this call site already passed a screen
// translation key as fallback (main.file_tree_node.toast_upload_failed) before T0010 — this
// regression test pins that the new resolveApiError-backed extractApiErrorMessage still
// never surfaces the raw server message for an unregistered/no-code error.

const { postFormRequest } = vi.hoisted(() => ({ postFormRequest: vi.fn() }))
vi.mock('@shared/api', async () => {
  const actual = await vi.importActual<typeof import('@shared/api')>('@shared/api')
  return { ...actual, postFormRequest }
})

const showToast = vi.fn()
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

const Host = defineComponent({
  setup(_, { expose }) {
    const upload = useFileUpload()
    expose(upload)
    return () => h('div')
  },
})

function mountHost() {
  return mount(Host, { global: { plugins: [i18n] } })
}

describe('useFileUpload upload error (0578 T0010)', () => {
  it('never shows the raw response.data.message; shows the toast_upload_failed fallback', async () => {
    postFormRequest.mockRejectedValue({
      response: { status: 500, data: { message: 'RAW SERVER TEXT' } },
    })
    const wrapper = mountHost()
    const file = new File(['x'], 'a.txt', { type: 'text/plain' })
    await (wrapper.vm as any).uploadFiles('prj_1', '/', [file], () => {})
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    const [message, level] = showToast.mock.calls[0]
    expect(message).not.toContain('RAW SERVER TEXT')
    expect(message).toBe(i18n.global.t('main.file_tree_node.toast_upload_failed'))
    expect(level).toBe('danger')
  })
})
