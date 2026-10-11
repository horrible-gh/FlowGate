import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import Tr2SourceImport from '@main/components/documents/Tr2SourceImport.vue'
import Tr2DocumentBody from '@main/components/documents/Tr2DocumentBody.vue'

const { postRequest, getRequest, putRequest, deleteRequest } = vi.hoisted(() => ({ postRequest: vi.fn(), getRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ postRequest, getRequest, putRequest, deleteRequest }))
const docId = 'flowgate.default.0640.0009-TR2'
const base = `/api/v1/documents/${docId}/tr2/sources`
const wrappers: Array<ReturnType<typeof mount>> = []
const byId = (w: ReturnType<typeof mount>, id: string) => w.find(`[data-testid="${id}"]`)
function body(over: Record<string, any> = {}) {
  const w = mount(Tr2SourceImport, { props: { docId, revision: 1, disabled: false, stale: false, ...over }, global: { plugins: [i18n] } })
  wrappers.push(w)
  return w
}
const sourceFile = (name: string, path = '', size = 4, read?: () => Promise<ArrayBuffer>) => ({ name, size, webkitRelativePath: path, arrayBuffer: read ?? (() => Promise.resolve(new Uint8Array([110, 101, 119, 10]).buffer)) })
async function load(w: ReturnType<typeof mount>, files: ReturnType<typeof sourceFile>[], folder = false) {
  const input = byId(w, folder ? 'tr2-source-folder' : 'tr2-source-files')
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change'); await flushPromises()
}
async function paths(w: ReturnType<typeof mount>, values: string[]) {
  const inputs = w.findAll('li > input')
  for (let i = 0; i < values.length; i++) await inputs[i].setValue(values[i])
}
function server(statuses: string[] = []) {
  postRequest.mockImplementation((url: string, payload: any) => Promise.resolve({ data: url.endsWith('/preview')
    ? { revision_no: payload.expected_revision, preview_fingerprint: 'fp', files: payload.files.map((file: any, index: number) => {
      const status = statuses[index] ?? 'CREATE'
      return { index, status, valid: status === 'CREATE' || status === 'EDIT', reason: status === 'review_required' ? 'manual review' : null,
        item: status === 'CREATE' || status === 'EDIT' ? { id: `s${index}`, kind: 'create_file', file: file.file, content: 'new\n' } : null }
    }) }
    : { new_revision: 2, imported_count: payload.selected.length } }))
}
beforeEach(() => { i18n.global.locale.value = 'en'; postRequest.mockReset(); getRequest.mockReset(); putRequest.mockReset(); deleteRequest.mockReset(); server() })
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers.length = 0; i18n.global.locale.value = 'en' })

describe('0640 source comparison', () => {
  it('requires explicit file path mapping and saves checked generated changes once', async () => {
    server(['EDIT', 'CREATE', 'unchanged', 'review_required'])
    const w = body()
    await load(w, ['a','b','c','d'].map(n => sourceFile(n)))
    expect(postRequest).not.toHaveBeenCalled()
    expect(w.findAll('li > input').every(i => (i.element as HTMLInputElement).value === '')).toBe(true)
    await paths(w, ['src/a','src/b','src/c','src/d'])
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(base+'/preview', { expected_revision: 1, files: ['a','b','c','d'].map(n => ({ file:`src/${n}`, content_base64:'bmV3Cg==' })) })
    const boxes = w.findAll('li input[type=checkbox]')
    expect(boxes.map(i => (i.element as HTMLInputElement).checked)).toEqual([true,true,false,false])
    expect(boxes[2].attributes('disabled')).toBeDefined()
    await boxes[1].setValue(false)
    expect(byId(w,'tr2-source-save').attributes('disabled')).toBeUndefined()
    await byId(w,'tr2-source-save').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[1]).toEqual([base, { expected_revision:1, files: ['a','b','c','d'].map(n => ({ file:`src/${n}`, content_base64:'bmV3Cg==' })), selected:[0], preview_fingerprint:'fp' }])
    expect(w.emitted('changed')?.[0]?.[0]).toMatchObject({ revision:2 })
    expect(w.findAll('li')).toHaveLength(0)
  })
  it('preserves folder-relative paths and supports 55 files', async () => {
    const w = body()
    await load(w, Array.from({length:55},(_,i)=>sourceFile(`a${i}`,`src/a${i}`)), true)
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    expect(w.findAll('li')).toHaveLength(55)
    expect(postRequest.mock.calls[0][1].files[54].file).toBe('src/a54')
    await byId(w,'tr2-source-save').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[1][1].selected).toHaveLength(55)
  })
  it('keeps read failures per file and compares the remaining file', async () => {
    const w = body()
    await load(w,[sourceFile('bad','src/bad',4,()=>Promise.reject(new Error('read'))),sourceFile('good','src/good')])
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[0][1].files).toHaveLength(1)
    expect(w.text()).toContain('Cannot read file')
    await byId(w,'tr2-source-save').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[1][1].selected).toEqual([0])
  })
  it('invalidates generated items after changing a mapped path', async () => {
    const w=body(); await load(w,[sourceFile('a','src/a')])
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    await paths(w,['src/b'])
    expect(byId(w,'tr2-source-save').attributes('disabled')).toBeDefined()
    expect(postRequest).toHaveBeenCalledTimes(1)
  })
  it.each([{revision:2},{stale:true},{disabled:true}])('blocks save when revision/stale/admission changes: %o',async change=>{
    const w=body(); await load(w,[sourceFile('a','src/a')])
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    await w.setProps(change)
    expect(byId(w,'tr2-source-save').attributes('disabled')).toBeDefined()
    await byId(w,'tr2-source-save').trigger('click')
    expect(postRequest).toHaveBeenCalledTimes(1)
  })
  it('cancel clears rows and permits selecting the same file again',async()=>{
    const w=body(); const files=[sourceFile('a','src/a')]
    await load(w,files); await byId(w,'tr2-source-cancel').trigger('click')
    expect(w.findAll('li')).toHaveLength(0)
    expect(postRequest).not.toHaveBeenCalled()
    await load(w,files); expect(w.findAll('li')).toHaveLength(1)
    expect((byId(w,'tr2-source-files').element as HTMLInputElement).value).toBe('')
  })
  it('ignores a late comparison response after document switch',async()=>{
    let resolve!: (v:any)=>void
    postRequest.mockImplementation(()=>new Promise(r=>{resolve=r}))
    const w=body(); await load(w,[sourceFile('a','src/a')])
    await byId(w,'tr2-source-preview').trigger('click')
    await w.setProps({docId:'flowgate.default.0640.0011-TR2'})
    resolve({data:{revision_no:1,preview_fingerprint:'old',files:[]}}); await flushPromises()
    expect(w.findAll('li')).toHaveLength(0)
    expect(w.emitted('changed')).toBeUndefined()
  })
  it('ignores late local reads after cancel',async()=>{
    let resolve!: (v:ArrayBuffer)=>void
    const w=body()
    const input=byId(w,'tr2-source-files')
    Object.defineProperty(input.element,'files',{value:[sourceFile('a','src/a',4,()=>new Promise(r=>{resolve=r}))],configurable:true})
    await input.trigger('change')
    await byId(w,'tr2-source-cancel').trigger('click')
    resolve(new Uint8Array([1]).buffer); await flushPromises()
    expect(w.findAll('li')).toHaveLength(0)
    expect(postRequest).not.toHaveBeenCalled()
  })
  it('ignores a late save result after document switch',async()=>{
    const w=body(); await load(w,[sourceFile('a','src/a')])
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    let resolve!: (v:any)=>void
    postRequest.mockImplementation(()=>new Promise(r=>{resolve=r}))
    await byId(w,'tr2-source-save').trigger('click')
    await w.setProps({docId:'flowgate.default.0640.0011-TR2'})
    resolve({data:{new_revision:2}}); await flushPromises()
    expect(w.emitted('changed')).toBeUndefined()
  })
  it('keeps the draft but requires comparison again after a drift error',async()=>{
    const w=body(); await load(w,[sourceFile('a','src/a')])
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    postRequest.mockRejectedValue({response:{status:409,data:{code:'tr2_source_drift'}}})
    await byId(w,'tr2-source-save').trigger('click'); await flushPromises()
    expect(w.findAll('li')).toHaveLength(1)
    expect(byId(w,'tr2-source-save').attributes('disabled')).toBeDefined()
    expect(w.emitted('changed')).toBeUndefined()
  })
  it('limits 201 files and total upload before reading',async()=>{
    const w=body(); const read=vi.fn(()=>Promise.resolve(new ArrayBuffer(1)))
    await load(w,Array.from({length:201},()=>sourceFile('a','src/a',1,read)))
    expect(read).not.toHaveBeenCalled(); expect(postRequest).not.toHaveBeenCalled()
    await load(w,Array.from({length:11},()=>sourceFile('a','src/a',1024*1024,read)))
    expect(read).not.toHaveBeenCalled(); expect(w.text()).toContain('10 MiB')
  })
  it('does not read an oversized file and allows a valid sibling',async()=>{
    const w=body(); const read=vi.fn(()=>Promise.resolve(new ArrayBuffer(1)))
    await load(w,[sourceFile('huge','src/huge',1024*1024+1,read),sourceFile('a','src/a')])
    expect(read).not.toHaveBeenCalled()
    await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[0][1].files).toHaveLength(1)
  })
  it.each(['ko','ja','en'])('renders localized controls: %s',async lang=>{
    i18n.global.locale.value=lang as 'ko'|'ja'|'en'
    const w=body(); await flushPromises()
    expect(w.text()).toContain(i18n.global.t('main.tr2_body.source.heading'))
    expect(w.text()).not.toContain('main.tr2_body.source.')
  })
  it('parent keeps source draft and blocks save after an SSE document update',async()=>{
    const current:any={document:{doc_id:docId,revision_no:1,doc_review_status:'pending_review',editable:true},mutation:{allowed:true,reason:null},
      readiness:{ready:false,code:null},body:{tr2_version:1,source_t2_doc_id:'flowgate.default.0640.0008-T2',edit_spec:{termination:'needs_more_work',edits:[],deferred:[],gate:{commands:[],apply:false}}},
      derived:{files:[],live_precheck:{drift:false}},approval:{latest_attempt:null,attempts:[],retry:{allowed:false,reason:'no_attempt'}},history:{source_history_state:'aligned',ledger:[]}}
    getRequest.mockResolvedValue({data:current})
    const w=mount(Tr2DocumentBody,{props:{tab:{id:docId,title:'TR2',type:'md',typeCode:'TR2',path:'document.json',projectId:'flowgate'},readOnly:false},global:{plugins:[i18n]}})
    wrappers.push(w); await flushPromises()
    await load(w,[sourceFile('a','src/a')]); await byId(w,'tr2-source-preview').trigger('click'); await flushPromises()
    window.dispatchEvent(new CustomEvent('fg:document_content_changed',{detail:{doc_id:docId,revision_no:2}})); await flushPromises()
    expect(w.findAll('[data-testid="tr2-source-row-0"]')).toHaveLength(1)
    expect(byId(w,'tr2-source-save').attributes('disabled')).toBeDefined()
    expect(postRequest).toHaveBeenCalledTimes(1)
  })
})
