/** Self-check: node tests/browser/conversation-participants.0154.mjs <scratch>.
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
const out = resolve(scratch, 'participants-browser')
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

  await call('Page.enable')
  await call('Emulation.setDeviceMetricsOverride', { width: 1200, height: 800, deviceScaleFactor: 1, mobile: false })
  await call('Page.navigate', { url: 'http://127.0.0.1:' + address.port + '/tests/browser/conversation-participants.0154.html' })
  await waitFor('window.__fixture?.ready')
  const settle = () => evaluate('new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
  const geometry = () => evaluate(`(() => {
    const row = document.querySelector('[data-test=conv-participants]');
    const track = row.querySelector('.conv-people-track');
    const r = track.getBoundingClientRect();
    const nodes = [...track.querySelectorAll('button')].filter(e => getComputedStyle(e).display !== 'none');
    const chips = nodes.filter(e => e.dataset.test === 'conv-participant');
    const more = track.querySelector('[data-test=conv-participants-more]');
    const rects = nodes.map(e => e.getBoundingClientRect());
    const allWidths = [...row.querySelector('.conv-people-measure-chips').children].map(e => e.getBoundingClientRect().width);
    return { shown: chips.length, more: more?.textContent.trim() || null,
      fits: rects.every((a, i) => a.left >= r.left - .1 && a.right <= r.right + .1 && (!i || a.left >= rects[i-1].right)),
      oneLine: rects.every(a => Math.abs(a.top - rects[0].top) < .1),
      roomForNext: more ? allWidths.slice(0, chips.length + 1).reduce((a,b) => a+b, 0) + 5 * (chips.length+1) + more.getBoundingClientRect().width <= r.width : false,
      labels: chips.map(e => e.title), rowHeight: row.getBoundingClientRect().height };
  })()`)
  for (const count of [1, 3, 9, 15]) {
    for (const width of [260, 330, 520]) {
      await evaluate(`window.__fixture.set(${width}, ${count})`)
      await settle()
      const g = await geometry()
      assert(g.fits && g.oneLine, JSON.stringify({ count, width, g }))
      assert.equal(g.more, g.shown === count ? null : '+' + (count - g.shown))
      assert(g.labels.every((label) => !/#\d|읽음/.test(label)), 'Chip tooltip shows a read position: ' + JSON.stringify(g.labels))
      assert(await evaluate('![...document.querySelectorAll("[data-test=conv-participant]")].some(e => /#/.test(e.textContent))'), 'Chip text shows #N')
      assert(!g.roomForNext, 'A complete chip unnecessarily folded: ' + JSON.stringify({ count, width, g }))
      if (g.more) {
        const before = await evaluate(`(() => { const e=document.querySelector('[data-test=fixture-timeline]'); e.scrollTop=80; return {top:e.getBoundingClientRect().top,height:e.clientHeight,scroll:e.scrollTop,input:document.querySelector('textarea').value}; })()`)
        await click('[data-test=conv-participants-more]')
        assert.equal(await evaluate('document.querySelectorAll("[data-test=conv-participant-detail]").length'), count)
        assert(await evaluate('!/#\\d/.test(document.querySelector("[data-test=conv-participants]").textContent)'), 'Chips or list show #N')
        const after = await evaluate(`(() => { const e=document.querySelector('[data-test=fixture-timeline]'); return {top:e.getBoundingClientRect().top,height:e.clientHeight,scroll:e.scrollTop,input:document.querySelector('textarea').value}; })()`)
        assert.deepEqual(after, before, 'Opening the list changes the timeline or draft')
        await call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
        await call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
        assert.equal(await evaluate('document.activeElement.dataset.test'), 'conv-participants-more')
      }
      results.push({ count, width, shown: g.shown, overflow: g.more })
    }
  }
  await evaluate('window.__fixture.set(260, 9)')
  await settle()
  await evaluate('document.querySelector("[data-test=conv-participants-more]").focus()')
  await call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
  await call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
  await waitFor('!!document.querySelector("[data-test=conv-participants-list]")')
  assert.equal(await evaluate('document.activeElement.dataset.participantKey'), 'fixture:0')
  await evaluate('window.__fixture.set(330, 15)')
  await settle()
  assert.equal(await evaluate('document.querySelectorAll("[data-test=conv-participant-detail]").length'), 15)
  await evaluate('window.__fixture.set(520, 3)')
  await settle()
  assert.equal(await evaluate('document.querySelectorAll("[data-test=conv-participant-detail]").length'), 3)
  await evaluate('window.__fixture.tab(false)')
  assert.equal(await evaluate('!!document.querySelector("[data-test=conv-participants-list]")'), false)
  await evaluate('window.__fixture.tab(true)')
  await settle()
  assert.equal((await geometry()).shown, 3)
  await call('Page.reload')
  await waitFor('window.__fixture?.ready')
  await settle()
  assert.equal((await geometry()).shown, 3)
  await evaluate('window.__fixture.longName()')
  await settle()
  assert.equal((await geometry()).more, '+1')
  assert.equal((await geometry()).fits, true)
  await click('[data-test=conv-participants-more]')
  assert(await evaluate('document.querySelector("[data-test=conv-participant-detail]").textContent.includes("Long model name")'))
  assert(await evaluate('!/fixture:long-key|123456|#/.test(document.querySelector("[data-test=conv-participants-list]").textContent)'))
  assert(await evaluate('document.querySelector("[data-test=conv-participant-detail]").title.includes("fixture:long-key")'))
  assert(await evaluate('document.querySelector("[data-test=conv-participants-list]").scrollWidth <= document.querySelector("[data-test=conv-participants-list]").clientWidth'))
  results.push('keyboard, live count changes, tab/remount, reload, long names and unchanged timeline/composer')
  await evaluate('window.__fixture.set(330, 15)')
  await settle()
  const screenshot = await call('Page.captureScreenshot', { format: 'png' })
  await writeFile(resolve(out, 'participants.png'), Buffer.from(screenshot.result.data, 'base64'))
  await writeFile(resolve(out, 'results.json'), JSON.stringify({ results }, null, 2))
  console.log(JSON.stringify({ ok: true, results, screenshot: resolve(out, 'participants.png') }))
} finally {
  ws?.close()
  if (chrome && chrome.exitCode === null && chrome.pid) {
    const stopped = new Promise(done => chrome.once('exit', done))
    chrome.kill()
    await stopped
  }
  await server.close()
}
