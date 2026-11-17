/** Self-check: node tests/browser/work-plan-ui.0074.mjs <scratch>.
 * Starts and closes its own Vite server and headless Chromium; all generated artifacts stay in scratch.
 */
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { createRequire } from 'node:module'
import { spawn } from 'node:child_process'
import { tmpdir } from 'node:os'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const scratch = process.argv[2] || resolve(tmpdir(), 'flowgate-card-ui-0044-' + Date.now())
if (!scratch) throw new Error('Pass the token scratch directory')
const out = resolve(scratch, 'browser')
await mkdir(out, { recursive: true })
const require = createRequire(import.meta.url)
const pkg = (name) => dirname(require.resolve(name + '/package.json'))
const server = await createServer({
  configFile: false, root: process.cwd(), cacheDir: resolve(out, 'vite-cache'),
  plugins: [vue()], define: { __FG_GUI_BUILD__: JSON.stringify('test') },
  resolve: { preserveSymlinks: true, alias: {
    '@': resolve('src'), '@shared': resolve('src/shared'), vue: pkg('vue'), 'vue-i18n': pkg('vue-i18n'), axios: pkg('axios'),
  } }, server: { host: '127.0.0.1', port: 0 },
})
let chrome, ws
const delay = (ms) => new Promise((done) => setTimeout(done, ms))
const results = []
try {
  await server.listen()
  const address = server.httpServer.address()
  const profile = resolve(out, 'profile-' + Date.now())
  const browserEnv = { ...process.env }
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
  await call('Page.enable')
  await call('Emulation.setDeviceMetricsOverride', { width: 1200, height: 1000, deviceScaleFactor: 1, mobile: false })
  await call('Page.navigate', { url: 'http://127.0.0.1:' + address.port + '/tests/browser/card-ui.0044.html' })
  await waitFor('window.__harnessReady === true && document.querySelectorAll("select.ai-base-select").length >= 7')
  const styles = await evaluate(`Array.from(document.querySelectorAll('select.ai-base-select')).map(e => ({height:e.getBoundingClientRect().height,align:getComputedStyle(e).alignItems,arrow:getComputedStyle(e,'::picker-icon').display,background:getComputedStyle(e).backgroundImage}))`)
  assert.equal(styles[0].height, 32);assert.equal(styles[1].height, 36)
  for (const style of styles) { assert.equal(style.align, 'center');assert.equal(style.arrow, 'none');assert(style.background.includes('linear-gradient')) }
  results.push('32/36px role, group and review count selectors centered with one shared arrow')
  await click('.question-policy header button')
  await waitFor('!!document.querySelector(".qp-top select")')
  assert.equal(await evaluate('getComputedStyle(document.querySelector(".qp-top select")).alignItems'), 'center')
  results.push('question assistant picker uses the same alignment')
  await evaluate('document.querySelector(".host32 select").focus()')
  await call('Input.dispatchKeyEvent', { type:'keyDown', key:'ArrowDown', code:'ArrowDown', windowsVirtualKeyCode:40 })
  await call('Input.dispatchKeyEvent', { type:'keyDown', key:'Enter', code:'Enter', windowsVirtualKeyCode:13 })
  await call('Input.dispatchKeyEvent', { type:'keyUp', key:'Enter', code:'Enter', windowsVirtualKeyCode:13 })
  await call('Input.dispatchKeyEvent', { type:'keyDown', key:'Escape', code:'Escape', windowsVirtualKeyCode:27 })
  const layout = await evaluate(`(() => {const card=document.querySelector('.flow-node-card'); const r=card.getBoundingClientRect(); const buttons=[...card.querySelectorAll('.flow-node-document')].map(e=>e.getBoundingClientRect()); const a=card.querySelector('.flow-node-approve').getBoundingClientRect(); return {height:r.height,rows:new Set(buttons.map(b=>b.top)).size,inside:buttons.every(b=>b.bottom<=r.bottom&&b.right<a.left),count:buttons.length}})()`)
  assert.equal(layout.count,12);assert(layout.rows>1);assert(layout.inside);assert(layout.height>92)
  await click('[data-doc-id="doc1"]')
  assert.deepEqual(await evaluate('window.__fixture.opened'), ['doc1'])
  results.push('12 document buttons wrap within measured card height, alongside approval, and open their own id')
  const shot = await call('Page.captureScreenshot', {format:'png'})
  await writeFile(resolve(out,'card-ui.png'),Buffer.from(shot.result.data,'base64'))
  await evaluate('window.__fixture.clear()')
  await waitFor('document.querySelectorAll(".flow-node-document").length === 0')
  results.push('document removal updates buttons without reload')
  await writeFile(resolve(out,'results.json'),JSON.stringify({results,styles,layout},null,2))
  console.log(JSON.stringify({ok:true,results,styles,layout,screenshot:resolve(out,'card-ui.png')}))
} finally {
  ws?.close();chrome?.kill();await server.close()
}
