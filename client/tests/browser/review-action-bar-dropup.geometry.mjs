import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const chromeCandidates = [
  process.env.CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean)
const chromePath = chromeCandidates.find(candidate => existsSync(candidate))
if (!chromePath) throw new Error('Chrome/Chromium not found. Set CHROME_PATH to run geometry regression tests.')

const profileDir = await mkdtemp(join(tmpdir(), 'flowgate-dropup-'))
const port = 9300 + Math.floor(Math.random() * 400)
const chrome = spawn(chromePath, [
  '--headless=new',
  '--disable-gpu',
  '--no-sandbox',
  '--no-first-run',
  '--no-default-browser-check',
  '--remote-allow-origins=*',
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profileDir}`,
  'about:blank',
], { stdio: 'ignore' })

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
let pageInfo
try {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
      if (response.ok) {
        pageInfo = await response.json()
        break
      }
    } catch {}
    await delay(100)
  }
  if (!pageInfo) throw new Error('Timed out connecting to headless Chrome')

  const socket = new WebSocket(pageInfo.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })

  let requestId = 0
  const pending = new Map()
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data)
    if (!message.id) return
    const callback = pending.get(message.id)
    if (!callback) return
    pending.delete(message.id)
    if (message.error) callback.reject(new Error(message.error.message))
    else callback.resolve(message.result)
  })
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++requestId
    pending.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
  })

  await call('Page.enable')
  await call('Runtime.enable')

  const modes = [
    { name: 'workflow', wrapWidth: 96.2, menuHeight: 130 },
    { name: 'next', wrapWidth: 83.7, menuHeight: 130 },
    { name: 'next-tsr-pending', wrapWidth: 100.7, menuHeight: 66 },
    { name: 'review-request', wrapWidth: 186.8, menuHeight: 98 },
  ]

  async function scenario(mode, legacy) {
    document.head.innerHTML = `
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        * { box-sizing: border-box; }
        html, body { margin: 0; width: 100%; height: 100%; }
        .actions {
          position: fixed; right: 24px; bottom: 10px; width: 400px; height: 29px;
          display: flex; justify-content: flex-end; align-items: center; overflow: visible;
        }
        .wrap { position: relative; display: inline-flex; flex: 0 0 auto; height: 29px; }
        .trigger { width: 100%; height: 29px; border: 0; }
        .menu {
          width: 140px; background: white; border: 1px solid #e2e8f0;
          border-radius: 6px; overflow: hidden; z-index: 1000;
        }
        .menu.legacy { position: absolute; bottom: calc(100% + 6px); right: 0; z-index: 200; }
        .item { display: block; width: 100%; border: 0; background: white; }
        @media (max-width: 760px) {
          .actions { left: 12px; right: 12px; width: auto; justify-content: flex-start; overflow-x: auto; }
        }
      </style>`
    document.body.innerHTML = ''

    const actions = document.createElement('div')
    actions.className = 'actions'
    const wrap = document.createElement('div')
    wrap.className = 'wrap'
    wrap.style.width = `${mode.wrapWidth}px`
    const trigger = document.createElement('button')
    trigger.className = 'trigger'
    wrap.append(trigger)
    actions.append(wrap)
    document.body.append(actions)

    const menu = document.createElement('div')
    menu.className = legacy ? 'menu legacy' : 'menu'
    menu.style.height = `${mode.menuHeight}px`
    for (let index = 0; index < 4; index += 1) {
      const item = document.createElement('button')
      item.className = 'item'
      item.style.height = `${mode.menuHeight / 4}px`
      menu.append(item)
    }

    if (legacy) {
      wrap.append(menu)
    } else {
      document.body.append(menu)
      menu.style.position = 'fixed'
      const triggerRect = trigger.getBoundingClientRect()
      const menuRect = menu.getBoundingClientRect()
      const margin = 8
      const maxLeft = innerWidth - margin - menuRect.width
      menu.style.left = `${Math.max(margin, Math.min(triggerRect.right - menuRect.width, maxLeft))}px`
      menu.style.top = `${Math.max(margin, triggerRect.top - 6 - menuRect.height)}px`
      window.addEventListener('orientationchange', () => menu.remove(), { once: true })
    }

    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    const rect = menu.getBoundingClientRect()
    const triggerRect = trigger.getBoundingClientRect()
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
    const clickable = Boolean(hit && (hit === menu || menu.contains(hit)))
    return {
      mode: mode.name,
      viewport: `${innerWidth}x${innerHeight}`,
      rect: [rect.left, rect.top, rect.right, rect.bottom].map(value => Number(value.toFixed(1))),
      triggerRight: Number(triggerRect.right.toFixed(1)),
      rightDelta: Number((rect.right - triggerRect.right).toFixed(1)),
      hit: hit?.className || null,
      overflow: [getComputedStyle(actions).overflowX, getComputedStyle(actions).overflowY],
      pass: rect.left >= 8 && rect.right <= innerWidth - 8 && rect.top >= 8 && clickable,
    }
  }

  const evaluateScenario = async (mode, legacy) => {
    const expression = `(${scenario.toString()})(${JSON.stringify(mode)}, ${legacy})`
    const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
    return result.result.value
  }
  const setViewport = (width, height) => call('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: false,
    screenWidth: width,
    screenHeight: height,
  })

  const legacyResults = []
  const fixedResults = []
  for (const [width, height] of [[375, 812], [360, 780]]) {
    await setViewport(width, height)
    for (const mode of modes) {
      legacyResults.push(await evaluateScenario(mode, true))
      fixedResults.push(await evaluateScenario(mode, false))
    }
  }

  await setViewport(1280, 800)
  for (const mode of modes) fixedResults.push(await evaluateScenario(mode, false))

  for (const width of [760, 761]) {
    await setViewport(width, 800)
    for (const mode of modes) fixedResults.push(await evaluateScenario(mode, false))
  }

  await setViewport(375, 812)
  await evaluateScenario(modes[3], false)
  await setViewport(812, 375)
  const orientationResult = await call('Runtime.evaluate', {
    expression: `window.dispatchEvent(new Event('orientationchange')); !document.querySelector('.menu')`,
    returnByValue: true,
  })
  const orientationClosed = orientationResult.result.value === true

  const legacyFailures = legacyResults.filter(result => !result.pass)
  const fixedFailures = fixedResults.filter(result => !result.pass)
  const desktopMisalignments = fixedResults.filter(
    result => result.viewport === '1280x800' && Math.abs(result.rightDelta) > 0.5,
  )

  if (legacyFailures.length !== legacyResults.length) {
    throw new Error(`Expected all ${legacyResults.length} legacy mobile cases to fail, got ${legacyFailures.length}`)
  }
  if (fixedFailures.length) throw new Error(`Fixed geometry failures: ${JSON.stringify(fixedFailures)}`)
  if (desktopMisalignments.length) throw new Error(`Desktop alignment failures: ${JSON.stringify(desktopMisalignments)}`)
  if (!orientationClosed) throw new Error('Menu remained open after orientationchange')

  console.log(JSON.stringify({
    chrome: chromePath,
    legacyExpectedFailures: legacyFailures.length,
    orientationClosed,
    results: fixedResults,
  }, null, 2))
  socket.close()
} finally {
  if (chrome.exitCode === null) {
    const exited = new Promise(resolve => chrome.once('exit', resolve))
    chrome.kill()
    await Promise.race([exited, delay(3000)])
  }
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      await rm(profileDir, { recursive: true, force: true })
      break
    } catch (error) {
      if (!['EBUSY', 'EPERM'].includes(error.code) || attempt === 9) throw error
      await delay(200)
    }
  }
}