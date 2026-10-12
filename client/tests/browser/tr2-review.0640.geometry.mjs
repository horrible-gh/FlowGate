// Render the live Vue SFCs with isolated API fixtures in headless Chrome.
// Compiler/runtime assets, browser profile and all evidence stay in --artifacts.
// This is a browser geometry/interaction check, not a substitute Vitest runner.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync, createWriteStream } from 'node:fs'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { createServer } from 'node:http'
import { stripTypeScriptTypes } from 'node:module'
import { dirname, isAbsolute, join, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const value = flag => process.argv[process.argv.indexOf(flag) + 1]
assert(process.argv.includes('--artifacts'), 'Pass an absolute scratch path with --artifacts')
const artifacts = value('--artifacts')
assert(isAbsolute(artifacts), 'Artifact path must be absolute')
const client = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const repo = resolve(client, '..')
assert(!resolve(artifacts).startsWith(repo + sep), 'Artifacts must be outside the source worktree')
await mkdir(artifacts, { recursive: true })
const vendors = join(artifacts, 'vendor')
await mkdir(vendors, { recursive: true })
async function asset(name, url) {
  const path = join(vendors, name)
  if (!existsSync(path)) {
    const response = await fetch(url, { signal: AbortSignal.timeout(45000) })
    assert(response.ok, `Dependency fetch failed: ${url} ${response.status}`)
    await writeFile(path, await response.text())
  }
  return readFile(path, 'utf8')
}
const runtime = await asset('vue.mjs', 'https://cdn.jsdelivr.net/npm/vue@3.4.38/dist/vue.esm-browser.prod.js')
await asset('compiler.mjs', 'https://cdn.jsdelivr.net/npm/@vue/compiler-sfc@3.4.38/dist/compiler-sfc.esm-browser.js')
const compiler = await import(pathToFileURL(join(vendors, 'compiler.mjs')).href)
const appStyle = (await readFile(join(client, 'shared/variables.css'), 'utf8')).replace(/^@import[^\r\n]+/gm, '') + await readFile(join(client, 'shared/app.css'), 'utf8')
const baseStyle = ':root{--sdb-w:0px}body{margin:0;font:14px/1.55 system-ui,"Malgun Gothic",sans-serif;color:#202b3a;background:#f5f7fb}*{box-sizing:border-box}.c-TR2{background:#536fe0}button,input,textarea,select{font:inherit}h4{margin:0}.card{background:white;border:1px solid #e3e9f1;border-radius:12px}.btn{background:white;border:1px solid #e3e9f1;border-radius:8px;padding:6px 10px;color:#48586c}.btn-primary{color:white;background:#356ae6}.btn-danger-outline{color:#b83b45}button:disabled{opacity:.45}.tr2-import-dialog[style*="display: none"]{display:none}'
// Use exactly the v4 demo dataset for visual comparison; long code is a separate case.
assert(process.argv.includes('--reference'), 'Pass the confirmed v4 HTML with --reference')
const referenceHtml = await readFile(value('--reference'), 'utf8')
const demoPathsText = referenceHtml.match(/const demoPaths=(\[[\s\S]*?\]);/)
assert(demoPathsText, 'Reference must contain the 50-file v4 fixture')
const demoPaths = [...demoPathsText[1].matchAll(/'([^']+)'/g)].map(match => match[1])
const referenceItems = Array.from({length:50}, (_,n) => {
  const i=n+1, kind=i<=32?'edit':i<=44?'create_file':'deferred'
  const file=demoPaths[n%demoPaths.length].replace(/(\.[^.]+)$/, `_demo${String(Math.floor(n/demoPaths.length)+1).padStart(2,'0')}$1`)
  const id=`sample_${String(i).padStart(3,'0')}`
  if(kind==='edit') return {id,kind,file,anchor_old:`function value_${i}() {\n  return ${i};\n}`,replacement_new:`function value_${i}() {\n  return ${i+1};\n}`,confidence:'high',rationale:`기존 로직 변경 (${i}/50)`}
  if(kind==='create_file') return {id,kind,file:`features/generated/${String(i).padStart(2,'0')}_${file.split('/').pop()}`,content:`// 예시 신규 파일 ${i}\nexport const sample = ${i};`,confidence:'medium',rationale:`신규 파일 (${i}/50)`}
  return {id,kind,file,reason:'needs_runtime',rationale:`실행환경 확인 필요 (${i}/50)`}
})
const pages = new Map()
const componentNames = ['AppIcon', 'Tr2DocumentBody', 'Tr2SourceImport', 'Tr2CodeDiff', 'Tr2ImportDialog', 'Tr2ItemEditor', 'Tr2RevisionPanel', 'ReviewActionBar']
for (const mode of ['after', ...(process.argv.includes('--before') ? ['before'] : [])]) {
  const prefix = `/${mode}`
  const styles = []
  const source = async path => {
    const snapshot = process.argv.includes('--before') && mode === 'before' ? join(value('--before'), path.replaceAll('/', '__')) : ''
    return readFile(snapshot && existsSync(snapshot) ? snapshot : join(repo, path), 'utf8')
  }
  for (let index = 0; index < componentNames.length; index++) {
    const name = componentNames[index]
    const text = await source(name === 'AppIcon' ? 'client/shared/AppIcon.vue' : `client/src/main/components/${name === 'ReviewActionBar' ? '' : 'documents/'}${name}.vue`)
    const { descriptor, errors } = compiler.parse(text, { filename: `${name}.vue` })
    assert.equal(errors.length, 0, `${mode}/${name}: ${errors}`)
    const id = `tr2-${mode}-${index}`
    let script = compiler.compileScript(descriptor, { id, inlineTemplate: true }).content
    script = stripTypeScriptTypes(script, { mode: 'transform' })
    if (name === 'AppIcon') script = script.replace("'./iconData'", "'" + prefix + "/iconData.js'").replace('import.meta.env.DEV', 'false')
    script = script.replace('export default', 'const component =')
    script += `\ncomponent.__scopeId='data-v-${id}';export default component;`
    pages.set(`${prefix}/modules/${name}.vue`, script)
    for (const style of descriptor.styles) {
      const result = compiler.compileStyle({ source: style.content, id: `data-v-${id}`, scoped: style.scoped })
      assert.equal(result.errors.length, 0)
      styles.push(result.code)
    }
  }
  // Compile the shared action bar itself; unrelated stores/icons/dialog shell are explicit mocks.
  pages.set(`${prefix}/modules/documents/tr2State`, stripTypeScriptTypes(await source('client/src/main/components/documents/tr2State.ts'), { mode: 'transform' }))
  pages.set(`${prefix}/modules/common/useToast`, 'export function useToast(){return {showToast:(...args)=>{window.toasts.push(args)}}}')
  pages.set(`${prefix}/modules/documents/testSpecRun`, 'export const cancelSpecRun=()=>{},reloadTestDocument=()=>{},specErrorKey=()=>null,startSpecRun=()=>{},testDocumentView=()=>null,specRunState=()=>({active:false,key:"idle"})')
  pages.set(`${prefix}/stores/docTypeStore`, 'export function useDocTypeStore(){return {getLabel:code=>code}}')
  pages.set(`${prefix}/stores/aiInvokeRuns`, 'export function useAiInvokeRunsStore(){return {isGroupRunning:()=>false,isGroupLeaseOrphaned:()=>false,refreshGroupLease:async()=>{}}}')
  pages.set(`${prefix}/composables/finalizeAxis`, stripTypeScriptTypes(await source('client/src/main/composables/finalizeAxis.ts'), { mode: 'transform' }))
  const emptyComponent = "export default {render(){return null}}"
  pages.set(`${prefix}/modules/GitFinalizeAxis.vue`, emptyComponent)
  pages.set(`${prefix}/modules/GitBranchMergeConflictHost.vue`, emptyComponent)
  pages.set(`${prefix}/icon.js`, pages.get(`${prefix}/modules/AppIcon.vue`))
  pages.set(`${prefix}/iconData.js`, stripTypeScriptTypes(await source('client/shared/iconData.ts'), {mode:'transform'}))
  pages.set(`${prefix}/merge-blocker.js`, 'export const isOpenableBranchMergeBlocker=()=>false,mergeTargetBlockerOf=()=>null')
  pages.set(`${prefix}/modules/ConfirmModal.vue`, `import {h} from 'vue';export default {props:['visible','message','confirmDisabled'],emits:['confirm','update:visible'],setup(p,{emit}){return ()=>p.visible?h('div',{class:'fixture-confirm',role:'alertdialog'},[h('p',p.message),h('button',{'data-testid':'fixture-approve-confirm',disabled:p.confirmDisabled,onClick:()=>{emit('confirm');emit('update:visible',false)}},'확인')]):null}}`)
  pages.set(`${prefix}/modules/tr2State`, stripTypeScriptTypes(await source('client/src/main/components/documents/tr2State.ts'), { mode: 'transform' }))
  pages.set(`${prefix}/locale.js`, stripTypeScriptTypes(await source('client/shared/i18n/ko.ts'), { mode: 'transform' }))
  pages.set(`${prefix}/i18n.js`, `import messages from './locale.js';function get(key){return key.split('.').reduce((v,k)=>v?.[k],messages)}export function useI18n(){return {t:(key,args={})=>String(get(key)??key).replace(/\\{([^}]+)\\}/g,(_,k)=>String(args[k]??'')),te:key=>typeof get(key)==='string'}}`)
  pages.set(`${prefix}/api.js`, `export const calls=[];export let model;export function setModel(m){model=m}const clone=v=>JSON.parse(JSON.stringify(v));export async function getRequest(url){calls.push({method:'GET',url});return {data: url.includes('/documents/detail?')?{document:clone(model.document)}:url.includes('/files/')?{before_text:'live source',truncated:false}:url.endsWith('/recovery')?{revision_no:model.document.revision_no,revisions:[{revision_no:model.document.revision_no,is_current:true,usable:true,origin:'human',created_at:null},{revision_no:1,is_current:false,usable:true,origin:'ai',created_at:null}],mutation:clone(model.mutation),raw:{available:false}}:url.includes('/revisions/')?{content:JSON.stringify(model.body.edit_spec)}:url.endsWith('/revisions')?{revisions:[]}:clone(model)}}export async function postRequest(url,payload){calls.push({method:'POST',url,payload:clone(payload)});if(url.endsWith('/review_transitions/approve')){const fail=window.failNextApproval&&!payload.request_key;window.failNextApproval=false;const attempt={attempt_id:'a'+(model.approval.attempts.length+1),approval_round:model.approval.attempts.length+1,document_revision:model.document.revision_no,state:fail?'failed':'succeeded',phase:fail?'validation':'commit',error_code:fail?'tr2_validation_failed':null,validation_json:JSON.stringify({commands:[{command:'pytest -q',exit_code:0,timed_out:false},{command:'npm test',exit_code:fail?1:0,timed_out:false}]}),retryable:fail,finished_at:'2026-10-12T06:30:00+09:00',commit_sha:fail?null:'abcdef1234567'};model.approval.attempts.push(attempt);model.approval.latest_attempt=attempt;model.approval.retry={allowed:fail,request_key:fail?'browser-retry-key':null};if(fail)throw {response:{status:422,data:{code:'tr2_validation_failed'}}};model.document.doc_review_status='approved';model.mutation.allowed=false;model.history.ledger=[{commit_sha:'abcdef1234567'}];return {data:{document:clone(model.document)}}}if(url.endsWith('/items/batch/preview'))return {data:{revision_no:model.document.revision_no,items:payload.items.map((entry,index)=>({index,valid:entry.item.id!=='duplicate',error:entry.item.id==='duplicate'?{code:'tr2_spec_invalid',details:{loc:'id',reason:'duplicate id'}}:null}))}};if(url.endsWith('/sources/preview'))return {data:{revision_no:model.document.revision_no,preview_fingerprint:'browser-fixture',files:payload.files.map((file,index)=>({index,status:!file.file?'review_required':index===2?'unchanged':index===3?'review_required':index===0?'EDIT':'CREATE',valid:!!file.file&&index<2,reason:!file.file?'tr2_path_unsafe':index===3?'multiple change hunks require manual review':null,item:!!file.file&&index<2?{id:'source-'+index,file:file.file,kind:index===0?'edit':'create_file',anchor_old:'old unique\\n',replacement_new:'new unique\\n',content:'created\\n',rationale:'fixture',confidence:'high'}:null}))}};if(url.endsWith('/items/batch')){for(const entry of payload.items)model.body.edit_spec[entry.collection].push(entry.item);model.document.revision_no++;return {data:{new_revision:model.document.revision_no}}}if(url.endsWith('/sources')){for(const index of payload.selected)model.body.edit_spec.edits.push({id:'source-'+index,file:payload.files[index].file,kind:index===0?'edit':'create_file',anchor_old:'old unique\\n',replacement_new:'new unique\\n',content:'created\\n'});model.document.revision_no++;return {data:{new_revision:model.document.revision_no}}}return {data:{ready:true,code:null}}}export async function putRequest(url,payload){calls.push({method:'PUT',url,payload});model.document.revision_no++;return {data:{new_revision:model.document.revision_no}}}export async function deleteRequest(url,payload){calls.push({method:'DELETE',url,payload});return {data:{new_revision:++model.document.revision_no}}}`)
  pages.set(`${prefix}/git-errors.js`, 'export function resolveGitError(){return "Fixture error"}')
  const fixture = {
    document: { doc_id: 'flowgate.default.0640.0009-TR2', revision_no: 2, doc_review_status: 'pending_review', editable: true }, mutation: { allowed: true, reason: null },
    readiness: { ready: true, code: null, reason: null, edits: [] }, gate_admission: { fingerprint: 'fixture', all_candidate: false, candidate_count: 0, commands: [{ command: 'pytest -q', state: 'registered' }, { command: 'npm test', state: 'registered' }] },
    body: { tr2_version: 1, source_t2_doc_id: 'flowgate.default.0640.0008-T2', baseline_fingerprint: 'fixture', edit_spec: { termination: 'ready_to_apply', gate: { apply: false, commands: ['pytest -q', 'npm test'] },
      edits: Array.from({ length: 42 }, (_, index) => index < 30 ? { id: `e${index}`, kind: 'edit', file: `server/modules/flow_gate/documents/example_${index}.txt`, anchor_old: '  const oldValue = "' + 'long original text '.repeat(20) + '";\n\treturn oldValue;\n', replacement_new: '  const newValue = "' + 'long replacement text '.repeat(20) + '";\n\treturn newValue;\n', rationale: '변경 근거', confidence: 'high' } : { id: `c${index}`, kind: 'create_file', file: `client/generated/example_${index}.txt`, content: 'created\n', rationale: '신규 파일', confidence: 'medium' }),
      deferred: Array.from({ length: 8 }, (_, index) => ({ id: `d${index}`, file: `src/later_${index}.txt`, reason: 'needs_runtime', rationale: '실행 환경 확인 필요' })) } },
    derived: { files: [], live_precheck: { drift: false, baseline_fingerprint: 'fixture', live_fingerprint: 'fixture' } }, approval: { attempts: [], latest_attempt: null, retry: { allowed: false, reason: 'no_attempt' } }, history: { source_history_state: 'aligned', ledger: [] },
  }
  fixture.body.edit_spec.edits = referenceItems.filter(item=>item.kind!=='deferred')
  fixture.body.edit_spec.deferred = referenceItems.filter(item=>item.kind==='deferred')
  const html = `<!doctype html><meta charset="utf-8"><style>${appStyle}${baseStyle}${styles.join('\n')}</style><script type="importmap">${JSON.stringify({ imports: { vue: '/vendor/vue.mjs', 'vue-i18n': `${prefix}/i18n.js`, '@shared/api': `${prefix}/api.js`, '@shared/gitErrors': `${prefix}/git-errors.js`, '@shared/AppIcon.vue': `${prefix}/icon.js`, '@shared/mergeTargetBlocker': `${prefix}/merge-blocker.js` } })}</script><div id="app"></div><script type="module">import {createApp,nextTick,h,ref} from 'vue';import Body from '${prefix}/modules/Tr2DocumentBody.vue';import Bar from '${prefix}/modules/ReviewActionBar.vue';import {setModel,calls,model} from '${prefix}/api.js';setModel(${JSON.stringify(fixture)});window.calls=calls;window.model=model;window.tick=nextTick;window.toasts=[];window.resetApproval=async()=>{model.document.doc_review_status='pending_review';model.mutation.allowed=true;model.approval={attempts:[],latest_attempt:null,retry:{allowed:false}};model.history.ledger=[];window.barEpoch.value++;await window.body.refresh()};const bodyRef=ref(null),barEpoch=ref(0),readOnly=ref(false);window.barEpoch=barEpoch;window.setReadOnly=async(value)=>{readOnly.value=value;await nextTick()};createApp({setup(){return ()=>h('div',{style:'padding:0 0 100px'},[h(Body,{ref:bodyRef,tab:{id:'flowgate.default.0640.0009-TR2',title:'반영안 검토',type:'md',typeCode:'TR2',path:'document.json',projectId:'flowgate'},readOnly:readOnly.value}),h(Bar,{key:barEpoch.value,mode:'review',docId:model.document.doc_id,docRef:model.document.doc_id,projectId:'flowgate',groupId:'flowgate.default.0640',docType:'TR2',reviewStatus:model.document.doc_review_status,commandAdmissionFingerprint:bodyRef.value?.commandAdmissionFingerprint,tr2ReviewSummary:bodyRef.value?.reviewSummary,onApprove:()=>bodyRef.value.refresh()})])}}).mount('#app');await nextTick();window.body=bodyRef.value;setTimeout(()=>window.ready=true,80);</script>`
  pages.set(`${prefix}/`, html)
  await writeFile(join(artifacts, `${mode}-harness.html`), html)
}
pages.set('/vendor/vue.mjs', runtime)
if (process.argv.includes('--reference')) pages.set('/reference/', (await readFile(value('--reference'), 'utf8')).replace('</head>', '<style>.nav,.topline,.demo-bar{display:none!important}.bottom-actionbar{left:0!important}.app{display:block}.main{padding:20px;gap:18px}.workspace{width:100%}</style></head>'))
const server = createServer((req, res) => {
  const body = pages.get(req.url)
  res.writeHead(body === undefined ? 404 : 200, { 'Content-Type': req.url.endsWith('/') ? 'text/html; charset=utf-8' : 'text/javascript; charset=utf-8' })
  res.end(body ?? 'not found')
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const url = `http://127.0.0.1:${server.address().port}`
const candidates = [process.env.CHROME_PATH, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'].filter(Boolean)
const browserPath = (process.argv.includes('--browser') ? value('--browser') : candidates.find(existsSync))
assert(browserPath, 'Headless Chrome/Edge is required')
const profile = join(artifacts, `profile-${Date.now()}`)
await mkdir(profile)
const port = 9450 + Math.floor(Math.random() * 300)
const browserLog = createWriteStream(join(artifacts, 'browser-process.log'), { flags: 'a' })
let browserExit = null
const chrome = spawn(browserPath, ['--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--no-default-browser-check', '--remote-allow-origins=*', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true })
chrome.stderr.pipe(browserLog)
chrome.on('exit', (code, signal) => { browserExit = { code, signal } })
chrome.on('error', error => { browserExit = { error: String(error) } })
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
let socket
try {
  let info
  for (let attempt = 0; attempt < 80; attempt++) {
    try { const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' }); if (response.ok) { info = await response.json(); break } } catch {}
    await delay(100)
  }
  assert(info, `Browser did not start: ${browserPath} ${JSON.stringify(browserExit)}; see browser-process.log`)
  socket = new WebSocket(info.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }) })
  let sequence = 0
  const pending = new Map()
  const errors = []
  socket.addEventListener('message', event => {
    const data = JSON.parse(event.data)
    if (data.method === 'Runtime.exceptionThrown') errors.push(data.params.exceptionDetails)
    const promise = pending.get(data.id)
    if (!promise) return
    pending.delete(data.id)
    data.error ? promise.reject(new Error(data.error.message)) : promise.resolve(data.result)
  })
  const call = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })) })
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails))
    return result.result.value
  }
  await call('Page.enable'); await call('Runtime.enable')
  const metrics = [], comparison = [], referenceGeometry = new Map(), referenceDialogWidth = new Map()
  const layout = mode => `(()=>{
    const ref=${mode === 'reference'}, q=s=>document.querySelector(s), rect=s=>{const r=q(s).getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height,bottom:r.bottom}}
    return {width:innerWidth,status:rect(ref?'#status-panel':'[data-testid=tr2-state-strip]'),stats:rect(ref?'.stats':'[data-testid=tr2-review-summary]'),review:rect(ref?'.content-card':'[data-testid=tr2-review-card]'),rail:rect(ref?'.files-col':'.tr2-file-column'),list:rect(ref?'#file-list':'[data-testid=tr2-file-list]'),diff:rect(ref?'.code-diff':'[data-testid=tr2-code-diff]'),detail:rect(ref?'.diff-col':'[data-testid=tr2-detail]'),background:getComputedStyle(q(ref?'.main':'[data-testid=tr2-body]')).backgroundColor,editColor:getComputedStyle(q(ref?'.file-row .badge':'.tr2-op-edit')).color,approveColor:getComputedStyle(q(ref?'#approve-btn':'[data-test=ab-approve]')).backgroundColor}
  })()`

  for (const width of [1024, 1440]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 1100, deviceScaleFactor: 1, mobile: false })
    for (const mode of ['reference', 'before', 'after']) {
      if (!pages.has(`/${mode}/`)) continue
      await call('Page.navigate', { url: `${url}/${mode}/` })
      for (let i = 0; i < 60; i++) { if (await evaluate(mode === 'reference' ? '!!document.querySelector("#file-list .file-row")' : 'window.ready===true')) break; await delay(100) }
      if (mode !== 'reference') {
        assert.equal(await evaluate('window.ready===true'), true, JSON.stringify(errors))
        await evaluate('document.querySelector("[data-testid=tr2-item-sample_001]").click()'); await delay(100)
      }
      const image = await call('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(artifacts, `${mode}-${width}.png`), Buffer.from(image.data, 'base64'))
      if (mode === 'reference') {
        referenceGeometry.set(width, await evaluate(layout(mode)))
        await evaluate('document.querySelector("#advanced").open=true;document.querySelector("#advanced").scrollIntoView({block:"end"})');await delay(30)
        const history=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(artifacts,`reference-history-${width}.png`),Buffer.from(history.data,'base64'))
        await evaluate('document.querySelector("#advanced").open=false;window.scrollTo(0,0)')
        await evaluate('document.querySelector("#add-bulk").click()');await delay(30)
        referenceDialogWidth.set(width,await evaluate('document.querySelector("#bulk-modal .modal").getBoundingClientRect().width'))
        const source=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(artifacts,`reference-source-dialog-${width}.png`),Buffer.from(source.data,'base64'))
        await evaluate('document.querySelector("[data-import-mode=json]").click()');await delay(30)
        const json=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(artifacts,`reference-json-dialog-${width}.png`),Buffer.from(json.data,'base64'))
        await evaluate('document.querySelector("[data-close=bulk-modal]").click()')
      }
      if (mode === 'after') {
        const actual = await evaluate(layout(mode)), expected = referenceGeometry.get(width)
        assert(expected, 'Capture v4 before comparing implementation')
        assert(Math.abs(actual.rail.width-expected.rail.width)<=2, 'File rail must match v4 34% split')
        assert(Math.abs((actual.diff.left-actual.detail.left)-(expected.diff.left-expected.detail.left))<=2, 'Code box must have v4 left inset')
        assert(actual.detail.width-(actual.diff.left-actual.detail.left)-actual.diff.width>=18, 'Code box needs right inset')
        assert.equal(actual.editColor, expected.editColor, 'EDIT color must match v4')
        assert.equal(actual.approveColor, expected.approveColor, 'Primary approval must match v4')
        assert.equal(actual.list.height, expected.list.height, '50-file list height must match v4')
        assert(Math.abs(actual.status.top-expected.status.top)<=5, 'Status panel vertical position must match v4')
        assert(Math.abs(actual.stats.top-expected.stats.top)<=5, 'Stats vertical position must match v4')
        assert(Math.abs(actual.review.top-expected.review.top)<=5, 'Review card vertical position must match v4')
        assert(Math.abs(actual.list.top-expected.list.top)<=5, 'Controls/list density must match v4')
        assert(Math.abs(actual.diff.height-expected.diff.height)<=2, 'Identical code lines must have v4 density')
        await evaluate('document.querySelector("[data-testid=tr2-open-import]").focus();document.querySelector("[data-testid=tr2-open-import]").click()');await delay(40)
        const dialogWidth=await evaluate('document.querySelector(".import-dialog").getBoundingClientRect().width')
        assert.equal(dialogWidth,referenceDialogWidth.get(width),'Import dialog width must match v4 at both sizes')
        const dialogShot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(artifacts,`empty-source-dialog-${width}.png`),Buffer.from(dialogShot.data,'base64'))
        await evaluate('document.querySelector(".import-dialog .close").click()');await delay(40)
        assert.equal(await evaluate('document.activeElement.dataset.testid'),'tr2-open-import','Closing dialog must restore focus')
        comparison.push({width,expected,actual,dialogWidth,referenceDialogWidth:referenceDialogWidth.get(width)})
        // Stress case is captured separately from the identical v4 dataset.
        await evaluate(`window.originalItem=structuredClone(window.model.body.edit_spec.edits[0]);window.model.body.edit_spec.edits[0].anchor_old='  <img src=x onerror="alert(1)"> '+ 'long original text '.repeat(20)+'\\n\\tindented\\n'+Array.from({length:80},(_,i)=>'before '+i).join('\\n')+'\\n';window.model.body.edit_spec.edits[0].replacement_new='  '+ 'long replacement text '.repeat(20)+'\\n\\tindented\\n'+Array.from({length:80},(_,i)=>'after '+i).join('\\n')+'\\n';window.body.refresh()`)
        await evaluate('document.querySelector("[data-testid=tr2-item-sample_001]").click()'); await delay(80)
        assert.equal(await evaluate('document.querySelector("[data-testid=tr2-code-diff] img")!==null'),false)
        assert.equal(await evaluate('document.querySelector("[data-testid=tr2-code-diff]").scrollHeight>document.querySelector("[data-testid=tr2-code-diff]").clientHeight'),true)
        assert.equal(await evaluate('document.querySelector(".code-before .code-scroll").scrollWidth>document.querySelector(".code-before .code-scroll").clientWidth'),true)
        assert.equal(await evaluate('Array.from(document.querySelectorAll(".code-before code")).map(line=>line.textContent).join("\\n")===window.model.body.edit_spec.edits[0].anchor_old'),true,'Code must preserve literal tabs/blank lines/trailing newline')
        await evaluate('document.querySelector("[data-testid=tr2-code-diff]").scrollTop=100000');await delay(30)
        assert.equal(await evaluate('(()=>{const panel=document.querySelector("[data-testid=tr2-code-diff]").getBoundingClientRect(),after=document.querySelector(".code-after").getBoundingClientRect();return after.top<panel.bottom&&after.bottom>panel.top})()'),true,'Vertical scroll must reach AFTER')
        await evaluate('document.querySelector("[data-testid=tr2-code-diff]").scrollTop=0');await delay(20)
        const longShot=await call('Page.captureScreenshot' ,{format:'png'});await writeFile(join(artifacts,`long-code-${width}.png`),Buffer.from(longShot.data,'base64'))

        const geometry = await evaluate(`(()=>{const q=id=>document.querySelector('[data-testid="'+id+'"]');const list=q('tr2-file-list'),diff=q('tr2-code-diff'),review=q('tr2-review-card');const scroll=diff.querySelector('.code-scroll');const line=diff.querySelector('code');return {width:innerWidth,count:list.querySelectorAll('li').length,listHeight:list.clientHeight,listScroll:list.scrollHeight,diffWidth:diff.clientWidth,codeWidth:scroll.scrollWidth,codePanelWidth:scroll.clientWidth,whiteSpace:getComputedStyle(line).whiteSpace,wordBreak:getComputedStyle(line).wordBreak,reviewWidth:review.clientWidth,bodyWidth:document.documentElement.scrollWidth,fileInputsHidden:Array.from(document.querySelectorAll('input[type=file]')).every(input=>getComputedStyle(input).display==='none')}})()`)
        assert.equal(geometry.count, 50); assert(geometry.listScroll > geometry.listHeight); assert(geometry.listHeight <= 475)
        assert.equal(geometry.whiteSpace, 'pre'); assert.equal(geometry.wordBreak, 'normal')
        assert(geometry.bodyWidth <= width); assert.equal(geometry.fileInputsHidden, true)
        const panels = await evaluate(`(()=>{const q=s=>document.querySelector(s),b=q('.code-before').getBoundingClientRect(),a=q('.code-after').getBoundingClientRect(),f=q('.sticky-footer-bar').getBoundingClientRect();return {beforeBottom:b.bottom,afterTop:a.top,beforeLeft:b.left,afterLeft:a.left,footerLeft:f.left,footerWidth:f.width,footerBottom:f.bottom,viewport:innerHeight,approveDisabled:q('[data-test=ab-approve]').disabled}})()`)
        assert(panels.afterTop >= panels.beforeBottom - 1, 'BEFORE/AFTER must stack at desktop width')
        assert.equal(panels.beforeLeft, panels.afterLeft)
        assert(panels.footerBottom <= panels.viewport + 1)
        assert.equal(panels.footerLeft, 0)
        assert(panels.footerWidth >= width - 16)
        assert.equal(panels.approveDisabled, false)
        const codeStyle=await evaluate(`(()=>{const block=document.querySelector('.tr2-code-block');return {padding:getComputedStyle(block).padding,blockWhiteSpace:getComputedStyle(block).whiteSpace}})()`)
        assert.equal(codeStyle.padding,'0px');assert.equal(codeStyle.blockWhiteSpace,'normal')
        metrics.push({...geometry,...panels,...codeStyle})
        await evaluate('window.model.body.edit_spec.edits[0]=window.originalItem;window.body.refresh()')
        await evaluate('document.querySelector("[data-testid=tr2-item-sample_001]").click()');await delay(50)
        // File and ID search, every operation filter, and selection retention.
        await evaluate(`const search=document.querySelector('[data-testid=tr2-file-search]');search.value='sample_044';search.dispatchEvent(new Event('input',{bubbles:true}))`);await delay(40)
        assert.equal(await evaluate('document.querySelectorAll("[data-testid=tr2-file-list] li").length'),1)
        await evaluate(`document.querySelector('[data-testid=tr2-clear-filters]').click()`);await delay(40)
        for(const [filter,count] of [['edit',32],['create_file',12],['deferred',6],['all',50]]){
          await evaluate(`document.querySelector('[data-testid=tr2-filter-${filter}]').click()`);await delay(30)
          assert.equal(await evaluate('document.querySelectorAll("[data-testid=tr2-file-list] li").length'),count)
        }

        await evaluate(`(()=>{const item=document.querySelector('[data-testid="tr2-item-sample_050"]');item.scrollIntoView({block:'nearest'});item.click()})()`); await delay(50)
        assert((await evaluate('document.querySelector("[data-testid=tr2-file-list]").scrollTop')) > 0)
        await evaluate(`document.querySelector('[data-testid="tr2-filter-edit"]').click()`); await delay(50)
        assert.equal(await evaluate('document.querySelector("[data-testid=tr2-selection-hidden]")!==null'), true)
        await evaluate(`document.querySelector('[data-testid="tr2-filter-all"]').click();document.querySelector('[data-testid="tr2-item-sample_001"]').click()`); await delay(50)
      }
    }
  }
  // Exercise actual File/ArrayBuffer APIs, drag/drop, path mapping, preview and save.
  await evaluate(`document.querySelector('[data-testid="tr2-open-import"]').click()`); await delay(70)
  await evaluate(`document.querySelector('[data-testid="tr2-import-tab-json"]').click();`)
  await evaluate(`(()=>{const dt=new DataTransfer();for(let i=0;i<50;i++){const item={id:'json-'+i,kind:'create_file',file:'src/json'+i+'.txt',content:'created',rationale:'fixture',confidence:'high'};dt.items.add(new File([i===49?'{invalid':JSON.stringify(item)],'item-'+i+'.json',{type:'application/json'}))}document.querySelector('[data-testid="tr2-json-dropzone"]').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt}))})()`)
  await delay(150)
  assert.equal(await evaluate('document.querySelectorAll("[data-testid^=tr2-batch-row-]").length'), 50)
  assert.equal(await evaluate('window.calls.filter(c=>c.method==="POST"&&c.url.endsWith("/items/batch")).length'), 0)
  assert.equal(await evaluate('document.querySelector("[data-testid=tr2-batch-row-49] .tr2-error")!==null'),true)
  assert.equal(await evaluate('document.querySelector("[data-testid=tr2-batch-select-49]").disabled'),true)
  assert.equal(await evaluate('document.querySelector("[data-test=ab-approve]").disabled'),true,'Draft import must block approval')
  const dialogImage = await call('Page.captureScreenshot', { format: 'png' }); await writeFile(join(artifacts, 'json-dialog-1440.png'), Buffer.from(dialogImage.data, 'base64'))
  await evaluate('document.querySelector("[data-testid=tr2-batch-row-49]").scrollIntoView({block:"nearest"})');await delay(30)
  const errorShot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(artifacts,'json-error-1440.png'),Buffer.from(errorShot.data,'base64'))
  await evaluate(`document.querySelector('[data-testid="tr2-batch-import"]').click()`); await delay(150)
  assert.equal(await evaluate('window.calls.filter(c=>c.method==="POST"&&c.url.endsWith("/items/batch")).length'), 1)
  assert.equal(await evaluate('window.calls.find(c=>c.url.endsWith("/items/batch")).payload.items.length'), 49)
  assert.equal(await evaluate('document.querySelectorAll("[data-testid=tr2-file-list] li").length'), 99)
  await evaluate(`document.querySelector('[data-testid="tr2-open-import"]').click()`); await delay(60)
  await evaluate(`(()=>{const dt=new DataTransfer();for(let i=0;i<4;i++)dt.items.add(new File(['new content\\n'],'source'+i+'.txt'));document.querySelector('[data-testid="tr2-source-dropzone"]').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt}))})()`); await delay(100)
  assert.equal(await evaluate('document.querySelector("[data-testid=tr2-source-row-0] > input").value'), '')
  await evaluate(`Array.from(document.querySelectorAll('[data-testid^="tr2-source-row-"] > input')).forEach((input,i)=>{input.value='src/source'+i+'.txt';input.dispatchEvent(new Event('input',{bubbles:true}))});document.querySelector('[data-testid="tr2-source-preview"]').click()`); await delay(100)
  assert.equal(await evaluate('window.calls.filter(c=>c.url.endsWith("/sources")).length'), 0)
  assert.equal(await evaluate('document.querySelector("[data-testid=tr2-source-row-2] input[type=checkbox]").disabled'),true)
  assert.equal(await evaluate('document.querySelector("[data-testid=tr2-source-row-3] .source-error")!==null'),true)
  const sourceImage = await call('Page.captureScreenshot', { format: 'png' }); await writeFile(join(artifacts, 'source-dialog-1440.png'), Buffer.from(sourceImage.data, 'base64'))
  await evaluate('document.querySelector("[data-testid=tr2-source-row-3]").scrollIntoView({block:"nearest"})');await delay(30)
  const reviewShot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(artifacts,'source-review-required-1440.png'),Buffer.from(reviewShot.data,'base64'))
  await evaluate(`document.querySelector('[data-testid="tr2-source-save"]').click()`); await delay(150)
  assert.deepEqual(await evaluate('window.calls.find(c=>c.url.endsWith("/sources")).payload.selected'), [0, 1])
  assert.equal(await evaluate('document.querySelectorAll("[data-testid=tr2-file-list] li").length'), 101)
  // Real shared approval handler + body refresh/retry handler. Only transport is mocked here.
  const capture = async name => { const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(artifacts,name+'.png'),Buffer.from(shot.data,'base64')) }
  await evaluate(`document.querySelector('[data-testid=tr2-advanced]').open=true`); await delay(70)
  await evaluate(`document.querySelector('[data-testid=tr2-advanced-tab-diagnostics]').click()`); await delay(40)
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('[data-testid=tr2-diagnostics-panel]')).display==='none'`),false)
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('[data-testid=tr2-history-panel]')).display==='none'`),true)
  await evaluate(`document.querySelector('[data-testid=tr2-advanced-tab-manage]').focus();document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}))`); await delay(40)
  assert.equal(await evaluate(`document.activeElement.dataset.testid`),'tr2-advanced-tab-history')
  await delay(60)
  assert.equal(await evaluate('document.querySelectorAll("[data-testid=tr2-revision-history] li").length'),2)
  await evaluate('(()=>{const extra=document.querySelector("[data-testid=tr2-revision-view-1]").closest("details");if(extra)extra.open=true;document.querySelector("[data-testid=tr2-revision-view-1]").click()})()');await delay(40)
  assert.equal(await evaluate('document.querySelector("[data-testid=tr2-revision-content-1]").textContent.includes("anchor_old")'),true)
  await evaluate('(()=>{const extra=document.querySelector("[data-testid=tr2-revision-view-1]").closest("details");if(extra)extra.open=true;document.querySelector("[data-testid=tr2-revision-view-1]").click()})()');await delay(30)

  await evaluate(`window.setReadOnly(true)`)
  assert.equal(await evaluate(`document.querySelector('[data-test=ab-approve]').disabled`),true)
  await evaluate(`window.setReadOnly(false)`)
  await evaluate(`document.querySelector('[data-test=ab-approve]').click()`); await delay(40)
  assert.equal(await evaluate(`window.calls.filter(c=>c.url.endsWith('/review_transitions/approve')).length`),0)
  await evaluate(`document.querySelector('[data-testid=fixture-approve-confirm]').click()`); await delay(180)
  assert.equal(await evaluate(`window.calls.find(c=>c.url.endsWith('/review_transitions/approve')).payload.expected_command_admission_fingerprint`),'fixture')
  assert.equal(await evaluate(`window.body.reviewSummary.title`),'승인 완료')
  assert.equal(await evaluate(`window.body.reviewSummary.description.includes('승인하면')`),false)
  assert.equal(await evaluate(`document.querySelector('[data-test=ab-approve]').disabled`),true)
  await evaluate(`document.querySelector('[data-testid=tr2-advanced]').scrollIntoView({block:'end'})`);await delay(50);await capture('approved-history-1440')
  assert.equal(await evaluate('document.querySelector("[data-testid=tr2-revision-restore-1]").disabled'),true)

  await evaluate(`window.resetApproval();window.failNextApproval=true`);await delay(120)
  await evaluate(`document.querySelector('[data-test=ab-approve]').click()`);await delay(40)
  await evaluate(`document.querySelector('[data-testid=fixture-approve-confirm]').click()`);await delay(180)
  assert.equal(await evaluate(`document.querySelector('[data-testid=tr2-retry]')!==null`),true)
  assert.equal(await evaluate(`document.querySelector('[data-test=ab-approve]').disabled`),true)
  await evaluate(`window.scrollTo(0,0)`);await delay(30);await capture('validation-failed-1440')
  await call('Emulation.setDeviceMetricsOverride',{width:1024,height:1100,deviceScaleFactor:1,mobile:false});await delay(50)
  assert((await evaluate('document.documentElement.scrollWidth'))<=1024,'Failure/retry banner must fit at 1024px')
  const failureGeometry=await evaluate(`(()=>{const banner=document.querySelector('[data-testid=tr2-state-strip]'),copy=banner.querySelector('.tr2-state-copy');return {copyWidth:copy.clientWidth,bannerHeight:banner.clientHeight}})()`)
  assert(failureGeometry.copyWidth>=250,'Failure explanation needs readable width')
  assert(failureGeometry.bannerHeight<=240,'Retry must wrap below the explanation, not squeeze it vertically')
  metrics.push({width:1024,failureGeometry})
  await capture('validation-failed-1024')
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});await delay(50)
  await evaluate(`document.querySelector('[data-testid=tr2-retry]').click()`);await delay(30)
  await evaluate(`document.querySelector('[data-testid=tr2-confirm-yes]').click()`);await delay(180)
  const approvals=await evaluate(`window.calls.filter(c=>c.url.endsWith('/review_transitions/approve'))`)
  assert.equal(approvals.length,3)
  assert.equal(approvals[2].payload.request_key,'browser-retry-key')
  assert.equal(await evaluate(`window.body.reviewSummary.title`),'승인 완료')
  assert.equal(await evaluate(`window.body.reviewSummary.description.includes('승인하면')`),false)
  assert.equal(await evaluate(`document.querySelectorAll('[data-testid=tr2-attempts] li').length`),2)
  await evaluate(`document.querySelector('[data-testid=tr2-advanced]').scrollIntoView({block:'end'})`);await delay(40);await capture('retry-success-history-1440')
  const interactionCalls = await evaluate('window.calls')
  const matrix = []
  const fixture3 = [{id:'e1',kind:'edit',file:'src/a.txt',anchor_old:'before line',replacement_new:'after line',confidence:'high',rationale:'기존 동작 수정'},{id:'c1',kind:'create_file',file:'src/new.txt',content:'brand new',confidence:'medium',rationale:'신규 파일 생성'},{id:'d1',kind:'deferred',file:'src/later.txt',reason:'needs_runtime',rationale:'실행 환경 확인 필요'}]
  const regionGeometry = ref => `(()=>{const q=s=>document.querySelector(s),rect=s=>{const e=q(s),r=e.getBoundingClientRect(),c=getComputedStyle(e);return {top:r.top,width:r.width,height:r.height,color:c.color,background:c.backgroundColor,fontSize:c.fontSize}};return {status:rect(${JSON.stringify(ref?'#status-panel':'[data-testid=tr2-state-strip]')}),review:rect(${JSON.stringify(ref?'.content-card':'[data-testid=tr2-review-card]')}),firstRow:rect(${JSON.stringify(ref?'.file-row':'.tr2-files li button')}),diff:rect(${JSON.stringify(ref?'.code-diff':'[data-testid=tr2-code-diff]')}),gate:rect(${JSON.stringify(ref?'.checks':'.tr2-checks')}),check:rect(${JSON.stringify(ref?'.check-item':'.tr2-gate li')}),footer:rect(${JSON.stringify(ref?'.bottom-actionbar':'.sticky-footer-bar')}),selected:q(${JSON.stringify(ref?'.file-row.active':'.tr2-files .is-selected button')})?.textContent,approveDisabled:q(${JSON.stringify(ref?'#approve-btn':'[data-test=ab-approve]')}).disabled,overflow:document.documentElement.scrollWidth>innerWidth}})()`
  for (const width of [1024,1440]) for (const count of [3,50]) for (const state of ['ready','validation_failed']) {
    const pair = {width,count,state}
    for (const mode of ['reference','after']) {
      await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false})
      await call('Page.navigate',{url:`${url}/${mode}/`})
      for(let i=0;i<60;i++){if(await evaluate(mode==='reference'?'!!document.querySelector("#file-list .file-row")':'window.ready===true'))break;await delay(100)}
      if(mode==='reference') {
        await evaluate(`const dataset=document.querySelector('#demo-dataset');dataset.value='${count}';dataset.dispatchEvent(new Event('change'));const state=document.querySelector('#demo-state');state.value='${state}';state.dispatchEvent(new Event('change'));window.scrollTo(0,0)`)
      } else {
        if(count===3) await evaluate(`window.model.body.edit_spec.edits=${JSON.stringify(fixture3.filter(x=>x.kind!=='deferred'))};window.model.body.edit_spec.deferred=${JSON.stringify(fixture3.filter(x=>x.kind==='deferred'))};window.body.refresh()`)
        if(state==='validation_failed') {
          await evaluate('window.failNextApproval=true;document.querySelector("[data-test=ab-approve]").click()');await delay(40)
          await evaluate('document.querySelector("[data-testid=fixture-approve-confirm]").click()');await delay(150)
        }
        await evaluate('window.scrollTo(0,0)')
      }
      await delay(60)
      await capture(`${mode}-${count}-${state}-${width}`)
      pair[mode]=await evaluate(regionGeometry(mode==='reference'))
      if(mode==='after') {
        assert.equal(pair.after.overflow,false,'Every count/state must fit')
        assert.equal(pair.after.approveDisabled,state!=='ready','Footer must obey actual readiness')
        assert(pair.after.selected?.includes(count===3?'a.txt':'tr2_service_demo01.py'),'First file must be selected without a test click')
        for(const region of ['status','review','firstRow','diff','gate','check','footer']) {
          assert(Math.abs(pair.after[region].height-pair.reference[region].height)<=3,`${width}/${count}/${state} ${region} height must match v4: ${JSON.stringify(pair)}`)
          if(region!=='footer') assert(Math.abs(pair.after[region].top-pair.reference[region].top)<=4,`${width}/${count}/${state} ${region} placement must match v4: ${JSON.stringify(pair)}`)
        }
        assert.equal(pair.after.status.background,pair.reference.status.background,'Failure and normal palette must match v4')
      }
      // Align the validation card at y=20 to expose lower sections under the same fixed footer.
      await evaluate("document.body.style.paddingBottom='1100px'") // identical capture-only scroll space, outside TR2
      await evaluate(`window.scrollTo(0,document.querySelector('${mode==='reference'?'.checks':'.tr2-checks'}').getBoundingClientRect().top+scrollY-20)`);await delay(40)
      await capture(`${mode}-${count}-${state}-commands-${width}`)
      await evaluate(mode==='reference'?'document.querySelector("#advanced").open=true':'document.querySelector("[data-testid=tr2-advanced]").open=true');await delay(80)
      await evaluate(`window.scrollTo(0,document.querySelector('${mode==='reference'?'#advanced':'[data-testid=tr2-advanced]'}').getBoundingClientRect().top+scrollY-20)`);await delay(40)
      await capture(`${mode}-${count}-${state}-history-${width}`)
      await evaluate(mode==='reference'?'document.querySelector("[data-tab=manage]").click()':'document.querySelector("[data-testid=tr2-advanced-tab-manage]").click()');await delay(40)
      await capture(`${mode}-${count}-${state}-manage-${width}`)
      if(mode==='after'&&state==='validation_failed') {
        await evaluate('document.querySelector("[data-testid=tr2-retry]").click()');await delay(30)
        await evaluate('document.querySelector("[data-testid=tr2-confirm-yes]").click()');await delay(150)
        assert.equal(await evaluate('window.body.reviewSummary.title'),'승인 완료')
        assert.equal(await evaluate('window.calls.filter(x=>x.url.endsWith("/review_transitions/approve")).length'),2)
        assert.equal(await evaluate('window.calls.filter(x=>x.url.endsWith("/review_transitions/approve"))[1].payload.request_key'),'browser-retry-key')
        await evaluate('window.scrollTo(0,0)');await capture(`after-${count}-retry-success-${width}`)
      }
    }
    matrix.push(pair)
  }
  await writeFile(join(artifacts,'matrix.json'),JSON.stringify(matrix,null,2))
  // Capture unchanged screenshots side by side at native pixel size, in the same browser.
  const gallery=[]
  for(const {width,count,state} of matrix) for(const region of ['', '-commands','-history','-manage']) {
    const stem=`${count}-${state}${region}-${width}`
    const refName=`reference-${stem}.png`,actualName=`after-${stem}.png`
    const left=(await readFile(join(artifacts,refName))).toString('base64'),right=(await readFile(join(artifacts,actualName))).toString('base64')
    const page=`/compare-${stem}/`
    pages.set(page,`<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:white;font:12px/32px system-ui;color:#202b3a}main{display:flex}section{width:${width}px;flex:none}header{height:32px;padding:0 20px;border-bottom:1px solid #e3e9f1}img{display:block;width:${width}px;height:1100px}</style><main><section><header>v4 원본 · ${count}개 · ${state} · ${width}×1100${region}</header><img src="data:image/png;base64,${left}"></section><section><header>수정 구현 · 동일 파일/선택/Revision/상태/해상도${region}</header><img src="data:image/png;base64,${right}"></section></main>`)
    await call('Emulation.setDeviceMetricsOverride',{width:width*2,height:1132,deviceScaleFactor:1,mobile:false})
    await call('Page.navigate',{url:url+page})
    await evaluate('Promise.all(Array.from(document.images).map(img=>img.decode()))')
    await capture(`compare-${stem}`)
    gallery.push(`<article><h2>${count} files · ${state} · ${width}px${region}</h2><a href="compare-${stem}.png"><img src="compare-${stem}.png"></a><p><a href="${refName}">v4 original</a> · <a href="${actualName}">Implementation</a></p></article>`)
  }
  await writeFile(join(artifacts,'comparison.html'),`<!doctype html><meta charset="utf-8"><title>TR0012 v4 comparison</title><style>body{margin:24px;background:#f5f7fb;color:#202b3a;font:14px/1.55 system-ui}article{margin:24px 0;padding:16px;background:white;border:1px solid #e3e9f1;border-radius:12px}h1{font-size:22px}h2{font-size:15px}img{display:block;max-width:100%;height:auto}a{color:#356ae6}</style><h1>TR0012 · v4 원본 / 수정 구현</h1><p>각 화면 ${1024}/${1440}×1100px, 배율 1. 원본 캡처를 변경하거나 축소하지 않고 좌우로 배치했습니다. API·외곽 셸은 fixture입니다. 본문/승인 바는 live worktree의 실제 Vue 컴포넌트입니다. 하단 영역 캡처는 양쪽 동일하게 해당 카드의 시작을 20px에 맞춥니다.</p>${gallery.join('')}`)

  assert.equal(errors.length, 0, JSON.stringify(errors))
  await writeFile(join(artifacts, 'metrics.json'), JSON.stringify({ comparison, metrics, calls: interactionCalls, errors }, null, 2))
  console.log('PASS: 3/50 files × ready/validation_failed × 1024/1440 same-condition matrix, initial selection and retry; confirmed v4 dataset/geometry/colors/density, search/all filters, code XSS/scroll, revision read/read-only restore; live Vue SFC rendering at 1024/1440, 50-row scroll, unwrapped code, hidden pickers, JSON drop/preview/save/reload and source drop/map/compare/save/reload, shared footer approval/validation failure/body retry, history tabs and read-only; API fixtures only.')
  console.log(`Evidence: ${artifacts}`)
} finally {
  socket?.close(); chrome.kill()
  await new Promise(resolve => server.close(resolve))
}
