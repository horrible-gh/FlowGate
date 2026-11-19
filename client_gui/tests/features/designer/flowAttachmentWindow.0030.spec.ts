import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Window from '@/features/designer/flowAttachments/FlowAttachmentWindow.vue'
import Host from '@/features/designer/flowAttachments/FlowAttachmentHost.vue'
import { flowAttachmentUi } from '@/features/designer/flowAttachments/state'
import { disposeFlowAttachmentOperations, getFlowState, pendingGroup, startFlowAttachmentOperations } from '@/features/designer/flowAttachments/operations'

const api = vi.hoisted(() => ({ readFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn(), downloadFile: vi.fn(), downloadZip: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => api)
vi.mock('@shared/api', () => ({ getRequest: vi.fn(async () => ({ data: { requests: [] } })) }))
vi.mock('@/features/designer/inspector/useInspectorResource', () => ({ useInspectorEvents: vi.fn() }))

const today = new Date(); today.setHours(9, 3, 0, 0)
const files = [
  { key: 'a', filename: 'shot.png', display_name: 'shot.png', doc_id: 'doc', size: 182400, content_type: 'image/png', uploaded_at: today.toISOString(), content_sha256: 'sha-a', source_type: 'CH', source_title: 'Chat', origin: 'ai' },
  { key: 'b', filename: 'notes.md', display_name: 'notes.md', doc_id: 'doc', size: 8400, content_type: 'text/markdown', uploaded_at: today.toISOString(), content_sha256: 'sha-b', source_type: 'R', source_title: 'Req', origin: 'requirement' },
]
const global = { plugins: [createI18n({ legacy: false, locale: 'ko', messages: { ko: {} } })], stubs: { AttachmentRequestItem: true, Teleport: true } }
const props = { flowId: 'A', projectId: 'p', title: '파일 첨부', position: 0, readOnly: false, current: true, focused: true, zIndex: 1 }
let wrapper: ReturnType<typeof mount> | undefined
beforeEach(() => { vi.clearAllMocks(); api.readFiles.mockResolvedValue(files.map(f => ({ ...f }))); api.deleteFile.mockResolvedValue({}); api.uploadFile.mockResolvedValue({}) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; disposeFlowAttachmentOperations(); vi.useRealTimers() })

describe('Flow attachment window (final mockup)', () => {
  it('draws the mockup header, count, type tiles, source tags and time', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    expect(wrapper.find('.fa-title').text()).toBe('첨부파일')
    expect(wrapper.find('.fa-flow').text()).toBe('파일 첨부')
    expect(wrapper.find('[data-test="flow-attachment-count"]').text()).toBe('2개')
    expect(wrapper.find('.fa-add').text()).toContain('파일 추가')
    const rows = wrapper.findAll('[data-test="flow-attachment-row"]')
    expect(rows[0].find('.fa-glyph').classes()).toContain('image')
    expect(rows[1].find('.fa-glyph').classes()).toContain('text')
    expect(rows[0].find('.fa-tag').text()).toBe('AI 첨부 승인')
    expect(rows[1].find('.fa-tag').text()).toBe('요구사항 카드')
    expect(rows[0].find('.fa-source').text()).toContain('09:03')
    expect(rows[0].find('.fa-size').text()).toBe('178 KB')
    // No text buttons for delete / undo: icons only, words in the tooltip.
    expect(wrapper.find('[data-test="flow-attachment-delete"]').text()).toBe('')
  })

  it('🗑 strikes the row and shows ↶ in its place; ↶ keeps the file, five seconds delete it', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    const row = wrapper.find('[data-test="flow-attachment-row"]')
    expect(row.classes()).toContain('gone')
    expect(row.find('[data-test="flow-attachment-undo"] .fa-ring').exists()).toBe(true)
    expect(row.find('[data-test="flow-attachment-delete"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="flow-attachment-count"]').text()).toBe('1개')
    await row.find('[data-test="flow-attachment-undo"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'a' }))
    expect(wrapper.findAll('[data-test="flow-attachment-row"]')).toHaveLength(1)
  })

  it('selection 🗑 turns into a numbered ↶ that brings all back; Ctrl+Z takes back the last delete', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-select-all"]').setValue(true)
    expect(wrapper.find('.fa-selected').text()).toBe('2개 선택')
    await wrapper.find('[data-test="flow-attachment-bulk-delete"]').trigger('click')
    const bulk = wrapper.find('[data-test="flow-attachment-bulk-undo"]')
    expect(bulk.find('.fa-undo-badge').text()).toBe('2')
    expect(wrapper.findAll('.fa-row.gone')).toHaveLength(2)
    await bulk.trigger('click')
    expect(wrapper.findAll('.fa-row.gone')).toHaveLength(0)
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await wrapper.find('[data-test="flow-attachment-window"]').trigger('keydown', { key: 'z', ctrlKey: true })
    expect(wrapper.findAll('.fa-row.gone')).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('a failed delete brings the row back with the reason', async () => {
    api.deleteFile.mockRejectedValueOnce({ response: { data: { error: { message: 'The file changed. Check it again.' } } } })
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('The file changed')
    expect(wrapper.findAll('[data-test="flow-attachment-row"]')).toHaveLength(2)
  })

  it('a name conflict asks in the window and overwrite sends the exact existing hash and key', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'existing', content_sha256: 'old' } } } } })
    wrapper = mount(Window, { props, global })
    await flushPromises()
    const file = new File(['replacement'], 'notes.md')
    const view = wrapper.vm as unknown as { add: (files: File[]) => Promise<void> }
    const work = view.add([file]); await flushPromises()
    const card = wrapper.find('[data-test="flow-attachment-conflict"]')
    expect(card.find('h3').text()).toBe('이름 중복')
    expect(card.findAll('button').map(b => b.text())).toEqual(['취소', '양쪽 보존', '덮어쓰기'])
    await card.find('[data-conflict="overwrite"]').trigger('click')
    await work; await flushPromises()
    expect(api.uploadFile).toHaveBeenLastCalledWith('A', file, 'overwrite', { key: 'existing', content_sha256: 'old' })
  })

  it('keep both uploads without an overwrite target', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'existing', content_sha256: 'old' } } } } })
    wrapper = mount(Window, { props, global })
    await flushPromises()
    const file = new File(['other'], 'notes.md')
    const work = (wrapper.vm as unknown as { add: (f: File[]) => Promise<void> }).add([file]); await flushPromises()
    await wrapper.find('[data-conflict="keep_both"]').trigger('click')
    await work; await flushPromises()
    expect(api.uploadFile).toHaveBeenLastCalledWith('A', file, 'keep_both', undefined)
  })
})

describe('Flow attachment window — failures kept while closed, close button, ring time (R3, R5)', () => {
  const inLocale = (locale: string) => ({ ...global, plugins: [createI18n({ legacy: false, locale, messages: { [locale]: {} } })] })
  const failDeleteWhileClosed = async (message?: string) => {
    api.deleteFile.mockRejectedValueOnce(message ? { response: { data: { error: { message } } } } : new Error('offline'))
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    wrapper.unmount(); wrapper = undefined // the window is closed before the five seconds are over
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    vi.useRealTimers()
  }

  it('a failure while the window is closed is the red line on top when it opens again, with the file back in the list', async () => {
    await failDeleteWhileClosed('The file changed.')
    wrapper = mount(Window, { props, global })
    await flushPromises()
    const line = wrapper.find('[data-test="flow-attachment-error"]')
    expect(line.attributes('role')).toBe('alert')
    expect(line.text()).toContain('삭제하지 못한 파일: shot.png')
    expect(line.text()).toContain('The file changed.')
    expect(wrapper.findAll('[data-test="flow-attachment-row"]')).toHaveLength(2)
    // the red line is above the list
    const html = wrapper.html()
    expect(html.indexOf('flow-attachment-errors')).toBeLessThan(html.indexOf('flow-attachment-list'))
  })

  it('has an accessible close button; it removes the line, and closing + reopening the window had not', async () => {
    await failDeleteWhileClosed('The file changed.')
    wrapper = mount(Window, { props, global })
    await flushPromises()
    wrapper.unmount()
    wrapper = mount(Window, { props, global })
    await flushPromises()
    const close = wrapper.find('[data-test="flow-attachment-error-close"]')
    expect(close.element.tagName).toBe('BUTTON')
    expect(close.attributes('type')).toBe('button')
    expect(close.attributes('aria-label')).toBe('오류 닫기')
    await close.trigger('click')
    expect(wrapper.find('[data-test="flow-attachment-error"]').exists()).toBe(false)
  })

  it.each([['en', 'Could not delete: shot.png', 'Could not complete the operation'], ['ja', '削除できなかったファイル: shot.png', '操作を完了できませんでした']])(
    'a failure without a server message is written in the language of the window that shows it (%s)', async (locale, lead, generic) => {
      await failDeleteWhileClosed()
      wrapper = mount(Window, { props, global: inLocale(locale) })
      await flushPromises()
      const text = wrapper.find('[data-test="flow-attachment-error"]').text()
      expect(text).toContain(lead); expect(text).toContain(generic)
    })

  it('the name-cancelled files are told in the same red area, and its close button clears them', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'k', content_sha256: 'h' } } } } })
    wrapper = mount(Window, { props, global })
    await flushPromises()
    const view = wrapper.vm as unknown as { add: (files: File[]) => void }
    view.add([new File(['x'], 'notes.md')])
    wrapper.unmount(); wrapper = undefined // closed while the name question is open: that file is cancelled
    await flushPromises()
    wrapper = mount(Window, { props, global })
    await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-errors"]').text()).toContain('이름이 같아 취소된 파일: notes.md')
    await wrapper.find('[data-test="flow-attachment-error-close"]').trigger('click')
    expect(wrapper.find('[data-test="flow-attachment-errors"]').exists()).toBe(false)
  })

  it('a successful delete afterwards removes the old line by itself', async () => {
    await failDeleteWhileClosed('The file changed.')
    wrapper = mount(Window, { props, global })
    await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-error"]').exists()).toBe(true)
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-error"]').exists()).toBe(false)
  })

  it('the ring time is a CSS variable on the window, set from the undo time (5 s)', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-window"]').attributes('style')).toContain('--fa-undo-ms: 5000ms')
  })
})


describe('Filtered attachment selection (0046)', () => {
  const dozen = Array.from({ length: 12 }, (_, i) => ({
    ...files[0], key: `file-${i}`, display_name: `${i < 7 ? 'opus' : 'other'}-${i}.txt`,
  }))
  const sevenKeys = dozen.slice(0, 7).map(f => f.key)
  const control = (name: string) => wrapper!.get(`[data-test="flow-attachment-${name}"]`)
  const allBox = () => control('select-all').element as HTMLInputElement
  const searchFor = (text: string) => control('search').setValue(text)
  const checkedKeys = () => wrapper!.findAll('[data-test="flow-attachment-row"]')
    .filter(row => (row.get('input').element as HTMLInputElement).checked).map(row => row.attributes('data-key'))
  beforeEach(async () => {
    api.readFiles.mockResolvedValue(dozen.map(f => ({ ...f })))
    wrapper = mount(Window, { props, global })
    await flushPromises()
  })

  it('counts and selects seven matches, then returns to the twelve-file scope', async () => {
    await searchFor(' OPUS ')
    expect(control('count').text()).toBe('7개')
    expect(wrapper!.findAll('.fa-row')).toHaveLength(7)
    await control('select-all').setValue(true)
    expect(checkedKeys()).toEqual(sevenKeys)
    expect(wrapper!.get('.fa-selected').text()).toBe('7개 선택')
    expect(allBox().checked).toBe(true)
    expect(allBox().indeterminate).toBe(false)
    await searchFor('')
    expect(control('count').text()).toBe('12개')
    expect(checkedKeys()).toEqual(sevenKeys)
    expect(allBox().checked).toBe(false)
    expect(allBox().indeterminate).toBe(true)
    await control('select-all').setValue(true)
    expect(checkedKeys()).toHaveLength(12)
  })

  it('zips only visible selected keys and preserves hidden selections', async () => {
    await control('select-all').setValue(true)
    await searchFor('opus')
    await control('zip').trigger('click')
    await flushPromises()
    expect(api.downloadZip).toHaveBeenCalledTimes(1)
    expect(api.downloadZip).toHaveBeenCalledWith('A', sevenKeys)
    await searchFor('')
    expect(checkedKeys()).toHaveLength(12)
  })

  it('deletes only seven visible files after the grace period, keeping the other five', async () => {
    vi.useFakeTimers()
    await control('select-all').setValue(true)
    await searchFor('opus')
    await control('bulk-delete').trigger('click')
    expect(wrapper!.findAll('.fa-row.gone')).toHaveLength(7)
    expect(control('bulk-undo').find('.fa-undo-badge').text()).toBe('7')
    expect(control('count').text()).toBe('0개')
    expect(allBox().disabled).toBe(true)
    expect(allBox().checked).toBe(false)
    expect(allBox().indeterminate).toBe(false)
    expect(control('zip').attributes('disabled')).toBeDefined()
    expect(api.deleteFile).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(api.deleteFile.mock.calls.map(([flow, file]) => [flow, file.key])).toEqual(sevenKeys.map(key => ['A', key]))
    await searchFor('')
    expect(control('count').text()).toBe('5개')
    expect(checkedKeys()).toEqual(dozen.slice(7).map(f => f.key))
  })

  it('adds and removes only visible selections and disables actions for hidden-only or no matches', async () => {
    await searchFor('other')
    await control('select-all').setValue(true)
    await searchFor('opus')
    expect(wrapper!.get('.fa-selected').text()).toBe('0개 선택')
    expect(allBox().checked).toBe(false)
    expect(allBox().indeterminate).toBe(false)
    expect(control('zip').attributes('disabled')).toBeDefined()
    expect(control('bulk-delete').attributes('disabled')).toBeDefined()
    await control('select-all').setValue(true)
    await control('select-all').setValue(false)
    await searchFor('missing')
    expect(control('count').text()).toBe('0개')
    expect(allBox().disabled).toBe(true)
    expect(allBox().checked).toBe(false)
    expect(allBox().indeterminate).toBe(false)
    expect(control('zip').attributes('disabled')).toBeDefined()
    expect(control('bulk-delete').attributes('disabled')).toBeDefined()
    await searchFor('')
    expect(checkedKeys()).toEqual(dozen.slice(7).map(f => f.key))
  })

  it('keeps pending rows visible and restores counts with individual, keyboard and bulk undo', async () => {
    vi.useFakeTimers()
    await control('select-all').setValue(true)
    await searchFor('opus')
    await control('bulk-delete').trigger('click')
    await wrapper!.find('[data-test="flow-attachment-undo"]').trigger('click')
    expect(control('count').text()).toBe('1개')
    expect(wrapper!.get('.fa-selected').text()).toBe('0개 선택')
    await control('window').trigger('keydown', { key: 'z', ctrlKey: true })
    expect(control('count').text()).toBe('7개')
    await control('select-all').setValue(true)
    await control('bulk-delete').trigger('click')
    await control('bulk-undo').trigger('click')
    expect(control('count').text()).toBe('7개')
    await searchFor('')
    expect(checkedKeys()).toEqual(dozen.slice(7).map(f => f.key))
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('preserves hidden selections through reload and upload, removing only absent keys', async () => {
    await control('select-all').setValue(true)
    await searchFor('opus')
    const view = wrapper!.vm as unknown as { reload: () => Promise<void>; add: (files: File[]) => Promise<void> }
    api.readFiles.mockResolvedValue(dozen.filter(f => f.key !== 'file-11'))
    await view.reload()
    api.readFiles.mockResolvedValue([...dozen.filter(f => f.key !== 'file-11'), { ...dozen[0], key: 'new', display_name: 'other-new.txt' }])
    await view.add([new File(['new'], 'other-new.txt')])
    await searchFor('')
    expect(control('count').text()).toBe('12개')
    expect(checkedKeys()).toEqual(dozen.slice(0, 11).map(f => f.key))
    expect((wrapper!.get('[data-key="new"] input').element as HTMLInputElement).checked).toBe(false)
  })
})


describe('Flow attachment host', () => {
  it('one window per Flow; while one is open, opening another Flow opens its window too', async () => {
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => false }, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await host.open(); await flushPromises()
    expect(wrapper.findAllComponents(Window)).toHaveLength(1)
    expect(flowAttachmentUi.currentOpen).toBe(true)
    await wrapper.setProps({ flowId: 'B', title: 'Flow B' }); await flushPromises()
    const windows = wrapper.findAllComponents(Window)
    expect(windows.map(w => w.props('flowId'))).toEqual(['A', 'B'])
    expect(windows.map(w => w.props('current'))).toEqual([false, true])
    expect(windows[1].props('focused')).toBe(true)
    const file = new File(['data'], 'sample.txt')
    await host.open([file]); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledWith('B', file)
    await windows[1].find('[data-test="flow-attachment-close"]').trigger('click')
    expect(flowAttachmentUi.currentOpen).toBe(false)
  })

  it('a file dropped anywhere shows the drop card and goes to the open Flow', async () => {
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => false }, global, attachTo: document.body })
    const file = new File(['x'], 'drop.txt')
    const transfer = { types: ['Files'], files: [file], dropEffect: 'none' }
    const over = new Event('dragover', { bubbles: true, cancelable: true }); Object.assign(over, { dataTransfer: transfer })
    document.body.dispatchEvent(over); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-drop-overlay"]').text()).toContain('Flow A')
    const drop = new Event('drop', { bubbles: true, cancelable: true }); Object.assign(drop, { dataTransfer: transfer })
    document.body.dispatchEvent(drop); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-drop-overlay"]').exists()).toBe(false)
    expect(api.uploadFile).toHaveBeenCalledWith('A', file)
  })
})

// flowgate.v02test.0036 T#1 (R1): the staged delete belongs to the Flow work manager, not to the window.
describe('Flow attachment window close / reopen (flowgate.v02test.0036)', () => {
  const hostProps = { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => false }
  const rowsOf = (w: ReturnType<typeof mount>) => w.findAll('[data-test="flow-attachment-row"]')

  it('🗑 → close at once: the delete still goes out after five seconds, and a reopened window no longer lists it', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await wrapper.find('[data-test="flow-attachment-close"]').trigger('click')
    expect(wrapper.findAllComponents(Window)).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(4999)
    expect(api.deleteFile).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(api.deleteFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'a', content_sha256: 'sha-a' }))
    vi.useRealTimers()
    api.readFiles.mockResolvedValue([{ ...files[1] }])
    await host.open(); await flushPromises()
    expect(rowsOf(wrapper).map(r => r.attributes('data-key'))).toEqual(['b'])
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
  })

  it('🗑 → close → reopen within five seconds: struck through with ↶, the ring goes on from the time left, ↶ keeps the file', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    const start = pendingGroup({ projectId: 'p', flowId: 'A' }, 'a')?.start
    expect(start).toBeTypeOf('number')
    await wrapper.find('[data-test="flow-attachment-close"]').trigger('click')
    await vi.advanceTimersByTimeAsync(2000)
    await host.open(); await flushPromises()
    const row = rowsOf(wrapper).find(r => r.attributes('data-key') === 'a')!
    expect(row.classes()).toContain('gone')
    const rect = row.find('[data-test="flow-attachment-undo"] .fa-ring rect')
    expect(rect.attributes('style')).toContain('animation-delay: -2000ms')
    // Reopening neither restarts the countdown nor moves its start.
    expect(pendingGroup({ projectId: 'p', flowId: 'A' }, 'a')?.start).toBe(start)
    await row.find('[data-test="flow-attachment-undo"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
    expect(rowsOf(wrapper).find(r => r.attributes('data-key') === 'a')!.classes()).not.toContain('gone')
  })

  it('a staged row the latest list no longer has keeps its ↶ (snapshot) until its time runs out', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await wrapper.find('[data-test="flow-attachment-close"]').trigger('click')
    api.readFiles.mockResolvedValue([{ ...files[1] }])
    await host.open(); await flushPromises()
    const row = rowsOf(wrapper).find(r => r.attributes('data-key') === 'a')
    expect(row?.classes()).toContain('gone')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'a', content_sha256: 'sha-a' }))
  })

  it('the window turning read-only drops the unsent delete; writable again does not bring it back', async () => {
    let locked = false
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    locked = true
    await wrapper.setProps({ readOnly: locked })
    await wrapper.setProps({ readOnly: false })
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
    expect(wrapper.findAll('.fa-row.gone')).toHaveLength(0)
  })

  it('a window that opens read-only (archive state not known yet) does not drop a staged delete by itself', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    wrapper.unmount()
    wrapper = mount(Window, { props: { ...props, readOnly: true }, global })
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
  })

  it('the Host follows the definite archive state while the window is closed', async () => {
    const archived: Record<string, boolean | null> = { A: false }
    wrapper = mount(Host, { props: { ...hostProps, isArchived: (id: string) => archived[id] ?? null }, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await wrapper.find('[data-test="flow-attachment-close"]').trigger('click')
    await wrapper.setProps({ isArchived: (id: string) => (id === 'A' ? true : null) })
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('🗑 → close → another Project → Designer left: the manager still follows the first Project\'s archive and sends nothing', async () => {
    const events = new Map<string, () => void>()
    startFlowAttachmentOperations({
      subscribe: (projectId, sub) => { events.set(projectId, () => sub.event()); return () => { events.delete(projectId) } },
      readArchived: async flowId => flowId === 'A',
    })
    wrapper = mount(Host, { props: { ...hostProps, isArchived: () => false }, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await wrapper.find('[data-test="flow-attachment-close"]').trigger('click')
    await wrapper.setProps({ projectId: 'q', flowId: 'Z', title: 'Flow Z' }) // another Project in the Designer
    wrapper.unmount(); wrapper = undefined // then the route leaves the Designer (Host gone, its archive path disconnected)
    await vi.advanceTimersByTimeAsync(1000)
    expect(events.has('p')).toBe(true)
    events.get('p')!() // another session archives Flow A
    await flushPromises()
    expect(pendingGroup({ projectId: 'p', flowId: 'A' }, 'a')).toBeUndefined()
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
    expect(events.has('p')).toBe(false) // no work left: the watch is gone
  })

  it('a Host remount (same session) keeps the countdown', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    wrapper.unmount()
    wrapper = mount(Host, { props: hostProps, global })
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+Z in a window takes back only its own Flow; row ↶ and the numbered ↶ keep working', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await flushPromises()
    await wrapper.setProps({ flowId: 'B', title: 'Flow B' }); await flushPromises()
    vi.useFakeTimers()
    const [winA, winB] = wrapper.findAllComponents(Window)
    await winA.find('[data-test="flow-attachment-delete"]').trigger('click')
    await winB.find('[data-test="flow-attachment-select-all"]').setValue(true)
    await winB.find('[data-test="flow-attachment-bulk-delete"]').trigger('click')
    // Ctrl+Z in A's window: A's latest group only; B's group stays.
    await winA.find('[data-test="flow-attachment-window"]').trigger('keydown', { key: 'z', ctrlKey: true })
    expect(winA.findAll('.fa-row.gone')).toHaveLength(0)
    expect(winB.findAll('.fa-row.gone')).toHaveLength(2)
    // Ctrl+Z inside the search box is the text's own undo.
    await winB.find('[data-test="flow-attachment-search"]').trigger('keydown', { key: 'z', ctrlKey: true })
    expect(winB.findAll('.fa-row.gone')).toHaveLength(2)
    await winB.find('[data-test="flow-attachment-bulk-undo"]').trigger('click')
    expect(winB.findAll('.fa-row.gone')).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
  })
})


describe('upload lifetime through the window and Host (R2)', () => {
  it('reopened window immediately shows the current file and remaining count', async () => {
    let finish!: () => void
    api.uploadFile.mockReturnValueOnce(new Promise<void>(resolve => { finish = resolve }))
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'A', isReadOnly: () => false }, global })
    const host = wrapper.vm as unknown as { open(files?: File[]): Promise<void> }
    await host.open([new File(['a'], 'first.txt'), new File(['b'], 'second.txt')]); await flushPromises()
    await wrapper.find('[data-test="flow-attachment-close"]').trigger('click')
    await host.open(); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-progress"]').text()).toContain('first.txt')
    expect(wrapper.find('[data-test="flow-attachment-progress"]').text()).toContain('1')
    finish(); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-test="flow-attachment-progress"]').exists()).toBe(false)
  })

  it('a drop captures its Flow before nextTick and survives Host unmount', async () => {
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'A', isReadOnly: () => false }, global })
    const file = new File(['a'], 'drop.txt')
    const event = new Event('drop', { bubbles: true, cancelable: true })
    Object.assign(event, { dataTransfer: { types: ['Files'], files: [file] } })
    document.dispatchEvent(event)
    const switching = wrapper.setProps({ flowId: 'B', projectId: 'q' })
    wrapper.unmount(); wrapper = undefined
    await switching; await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledWith('A', file)
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
  })

  it('Host unmount cancels only the active conflict and the remaining uploads continue', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'old', content_sha256: 'sha' } } } } })
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'A', isReadOnly: () => false }, global })
    const host = wrapper.vm as unknown as { open(files?: File[]): Promise<void> }
    await host.open()
    await host.open([new File(['a'], 'a.txt'), new File(['b'], 'b.txt')]); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-conflict"]').exists()).toBe(true)
    wrapper.unmount(); wrapper = undefined
    await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(2)
    expect(api.uploadFile.mock.calls[1][1].name).toBe('b.txt')
  })
})

describe('Flow attachment window — closing with the header × while a name question is open', () => {
  const conflict = { response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'old', content_sha256: 'sha' } } } } }

  it('the real × button closes the window; only the asked file is cancelled and the rest upload', async () => {
    api.uploadFile.mockRejectedValueOnce(conflict)
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'A', isReadOnly: () => false }, global })
    const host = wrapper.vm as unknown as { open(files?: File[]): Promise<void> }
    await host.open()
    await host.open([new File(['a'], 'a.txt'), new File(['b'], 'b.txt'), new File(['c'], 'c.txt')]); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-conflict"]').exists()).toBe(true)
    // The header's own close button (not the conflict card's buttons, not a DOM shortcut).
    const close = wrapper.find('[data-test="flow-attachment-close"]')
    expect(close.exists()).toBe(true)
    expect(close.element.closest('.fa-conflict')).toBeNull()
    await close.trigger('click'); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-window"]').exists()).toBe(false)
    expect(api.uploadFile.mock.calls.map(call => call[1].name)).toEqual(['a.txt', 'b.txt', 'c.txt'])
    expect(api.uploadFile.mock.calls.filter(call => call[1].name === 'a.txt')).toHaveLength(1)
    expect(getFlowState({ projectId: 'p', flowId: 'A' }).cancelledUploads).toEqual(['a.txt'])
    expect(getFlowState({ projectId: 'p', flowId: 'A' }).collision).toBeNull()
  })

  it('the header is stacked above the conflict card, so the pointer reaches × while the card is up', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const source = readFileSync(resolve(__dirname, '../../../src/features/designer/flowAttachments/FlowAttachmentWindow.vue'), 'utf8')
    const z = (selector: string) => Number(new RegExp(`${selector.replace('.', '\\.')}\\s*\\{[^}]*?z-index:\\s*(\\d+)`).exec(source)?.[1])
    const head = /\.fa-head \{([^}]*)\}/.exec(source)?.[1] ?? ''
    expect(head).toMatch(/position:\s*relative/)
    expect(z('.fa-head')).toBeGreaterThan(z('.fa-conflict'))
  })
})
