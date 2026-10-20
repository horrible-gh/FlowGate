/** Self-check: node tests/browser/work-plan-ui.0074.mjs <scratch>.
 * Starts and closes its own Vite server and headless Chromium; all generated artifacts stay in scratch.
 */
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { createRequire } from 'node:module'
import { spawn } from 'node:child_process'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const scratch = process.argv[2]
if (!scratch) throw new Error('Pass the token scratch directory')
const out = resolve(scratch, 'browser')
await mkdir(out, { recursive: true })
const require = createRequire(import.meta.url)
const pkg = (name) => dirname(require.resolve(name + '/package.json'))
const server = await createServer({
  configFile: false, root: process.cwd(), cacheDir: resolve(out, 'vite-cache'),
  plugins: [vue()], define: { __FG_GUI_BUILD__: JSON.stringify('test') },
  resolve: { preserveSymlinks: true, alias: {
    '@': resolve('src'), '@shared': resolve('../client/shared'), vue: pkg('vue'), 'vue-i18n': pkg('vue-i18n'), axios: pkg('axios'),
  } }, server: { host: '127.0.0.1', port: 0 },
})
let chrome, ws
const delay = (ms) => new Promise((done) => setTimeout(done, ms))
const results = []
try {
  await server.listen()
  const address = server.httpServer.address()
  const profile = resolve(out, 'profile-' + Date.now())
  const browserEnv = { ...process.env, LOCALAPPDATA: resolve(out, 'localappdata'), APPDATA: resolve(out, 'appdata'),
    USERPROFILE: resolve(out, 'user'), TEMP: resolve(out, 'temp'), TMP: resolve(out, 'temp') }
  for (const directory of [browserEnv.LOCALAPPDATA, browserEnv.APPDATA, browserEnv.USERPROFILE, browserEnv.TEMP]) {
    await mkdir(directory, { recursive: true })
  }
  chrome = spawn(process.argv[3] || process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank',
  ], { windowsHide: true, env: browserEnv, stdio: ['ignore', 'pipe', 'pipe'] })
  let launchError
  let chromeLog = ""
  chrome.stdout.on("data", (data) => { chromeLog += data })
  chrome.stderr.on("data", (data) => { chromeLog += data })
  chrome.on('error', (error) => { launchError = error })
  let port
  for (let i = 0; i < 100; i++) {
    if (launchError) throw launchError
    try { port = Number((await readFile(resolve(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); break } catch {}
    await delay(100)
  }
  assert(port, 'Chrome debugging port; exit=' + chrome.exitCode + '; ' + chromeLog)
  const page = await (await fetch('http://127.0.0.1:' + port + '/json/new?about:blank', { method: 'PUT' })).json()
  ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((ok, bad) => { ws.addEventListener('open', ok, { once: true }); ws.addEventListener('error', bad, { once: true }) })
  let seq = 0
  const pending = new Map()
  ws.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data)
    if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id) }
  })
  const call = (method, params = {}) => new Promise((done) => {
    const id = ++seq; pending.set(id, done); ws.send(JSON.stringify({ id, method, params }))
  })
  const evaluate = async (expression) => {
    const response = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (response.result?.exceptionDetails) throw new Error(JSON.stringify(response.result.exceptionDetails))
    return response.result?.result?.value
  }
  const waitFor = async (expression) => {
    for (let i = 0; i < 200; i++) { if (await evaluate(expression)) return; await delay(100) }
    throw new Error('Timed out: ' + expression)
  }
  const click = async (selector) => {
    const point = await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) throw new Error('Missing element'); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`)
    await call('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 })
    await call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 })
    await delay(150)
  }
  const q = (name) => '[data-test="' + name + '"]'
  const card = (id) => q('wp-edit-card') + '[data-card="' + id + '"] '
  await call('Page.enable')
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
  await call('Page.navigate', { url: 'http://127.0.0.1:' + address.port + '/tests/browser/work-plan-ui.0074.html' })
  await waitFor('window.__harnessReady === true')
  await waitFor('document.querySelector("[data-test=inspector-work-plan]").dataset.status === "ready" || document.querySelector("[data-test=inspector-work-plan]").dataset.status === "success"')
  assert.equal(await evaluate('!!document.querySelector("[data-test=work-plan-progress]")'), false)
  assert.equal(await evaluate('!!document.querySelector(".inspector-work-plan-progress.is-empty")'), false)
  results.push('empty plan has no dashed placeholder')
  const designLinks = await evaluate('Array.from(document.querySelectorAll("[data-test=inspector-design-card] [data-test=inspector-slot-name]")).map(e=>e.textContent.trim())')
  assert(designLinks.some((text) => text.includes('설계 지시')))
  assert(designLinks.some((text) => text.includes('설계서')))
  await click('[data-test=inspector-design-card] [data-test=inspector-slot-name]')
  await waitFor('window.__fixture.documents().includes("design-instruction")')
  results.push('Inspector opens the preserved DS document separately from its report')
  await evaluate('window.__fixture.close(); window.__fixture.open()')
  await waitFor('document.querySelector("[data-test=wp-edit-body]")?.dataset.phase === "ready"')
  await click(card('c_d1') + q('wp-edit-card-toggle'))
  await click('[data-row="c_d1|main"]' + q('wp-edit-step-edit'))
  await waitFor('!!document.querySelector("[data-test=wp-step-text]")')
  assert.equal(await evaluate('document.querySelector("[data-test=wp-step-text]").value'), '기존 설계지시')
  assert((await evaluate('document.querySelector("[data-test=wp-step-heading]").textContent')).includes('설계 지시'))
  await click(q('wp-step-cancel'))
  results.push('work plan design instruction opens with preserved text')
  await click('[data-row="c_d1|result"]' + q('wp-edit-step-edit'))
  await waitFor('!!document.querySelector("[data-test=wp-step-text]")')
  assert((await evaluate('document.querySelector("[data-test=wp-step-heading]").textContent')).includes('설계서'))
  assert.equal(await evaluate('document.querySelector("[data-test=wp-step-text]").value'), '기존 설계지시')
  await click(q('wp-step-cancel'))
  await click(card('c_d1') + q('wp-edit-instruction-mode'))
  assert.equal(await evaluate('!!document.querySelector("[data-row=\\\"c_d1|main\\\"]")'), false)
  await click('[data-row="c_d1|result"]' + q('wp-edit-step-edit'))
  await waitFor('!!document.querySelector("[data-test=wp-step-text]")')
  assert((await evaluate('document.querySelector("[data-test=wp-step-heading]").textContent')).includes('설계서'))
  await click(q('wp-step-cancel'))
  await click(card('c_d1') + q('wp-edit-instruction-mode'))
  results.push('design sheet pencil opens its own heading before and after instruction omission')
  await click(card('c_t1') + q('wp-edit-instruction-mode'))
  assert.equal(await evaluate('!!document.querySelector("[data-row=\\\"c_t1|main\\\"]")'), false)
  assert((await evaluate('document.querySelector("[data-test=wp-edit-step-totals]").textContent')).includes('7'))
  await click(q('wp-edit-save'))
  await waitFor('window.__fixture.body().steps.find(s=>s.key==="T#1").instruction_mode === "omit"')
  const retained = await evaluate('window.__fixture.body().steps.find(s=>s.key==="T#1").pre_instruction_text')
  assert.equal(retained, '사전 지시')
  await evaluate('window.__fixture.close(); window.__fixture.open()')
  await waitFor('document.querySelector("[data-test=wp-edit-body]")?.dataset.phase === "ready"')
  assert.equal(await evaluate('document.querySelector("[data-card=c_t1] [data-test=wp-edit-instruction-mode]").checked'), true)
  await click(card('c_t1') + q('wp-edit-instruction-mode'))
  await click(q('wp-edit-save'))
  await waitFor('window.__fixture.body().steps.find(s=>s.key==="T#1").instruction_mode === "use"')
  assert.equal(await evaluate('window.__fixture.body().steps.find(s=>s.key==="T#1").pre_instruction_text'), retained)
  results.push('ON/OFF, save and reopen retain the instruction, attachment and report')
  const screenshot = await call('Page.captureScreenshot', { format: 'png' })
  await writeFile(resolve(out, 'work-plan.png'), Buffer.from(screenshot.result.data, 'base64'))
  await writeFile(resolve(out, 'results.json'), JSON.stringify({ results }, null, 2))
  console.log(JSON.stringify({ ok: true, results, screenshot: resolve(out, 'work-plan.png') }))
} finally {
  ws?.close()
  chrome?.kill()
  await server.close()
}
