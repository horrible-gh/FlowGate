// Capture the real application on a local test server, without API mocks.
// node tests/visual/themes/capture.mjs <test-url> <scratch-out-dir> <test-storage-state.json>
// Requires Playwright + Chromium already available in the test environment.
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { isAbsolute, relative, resolve, join } from 'node:path'
const [urlArg, outArg, state] = process.argv.slice(2)
if (!urlArg || !outArg || !state) throw new Error('Provide local test URL, scratch evidence directory, and authenticated test storage state')
const url = new URL(urlArg)
if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error('Only a local test server is allowed')
const out = resolve(outArg)
const rel = relative(resolve('..'), out)
if (!isAbsolute(outArg) || (!rel.startsWith('..') && !isAbsolute(rel))) throw new Error('Evidence must be in an absolute scratch directory outside the source tree')
const { chromium } = await import('playwright')
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ storageState: state, viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
const page = await context.newPage()
const failures = []
page.on('pageerror', error => failures.push(error.message))
const evidence = { capturedAt: new Date().toISOString(), browser: browser.version(), url: url.origin + url.pathname, viewport: { width: 1440, height: 1000 }, captures: [], errors: failures }
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
evidence.sourceHashes = Object.fromEntries(['src/features/designer/shell/DesignerTaskbar.vue', 'src/features/settings/components/MeThemePanel.vue'].map(p => [p, hash(readFileSync(p))]))
mkdirSync(out, { recursive: true })
let original
async function themePanel() {
  await page.locator('.menu-trigger').click()
  // Classic renders cards only after opening their group; labels may be localized.
  const taskbar = page.locator('[data-test="designer-taskbar"]')
  if (await taskbar.getAttribute('data-taskbar-style') === 'classic') {
    await page.locator('.classic-group[data-group="settings"]').click()
    await page.locator('.start-classic-sub [data-card="theme"]').waitFor()
  }
  await page.locator('[data-card="theme"]').click()
  await page.locator('[data-test="taskbar-style-card"][aria-checked="true"]').waitFor()
}
async function choose(style) {
  const card = page.locator(`[data-test="taskbar-style-card"][data-style="${style}"]`)
  if (await card.getAttribute('aria-checked') !== 'true') {
    const saved = page.waitForResponse(r => r.url().includes('ui-settings') && ['PUT', 'PATCH', 'POST'].includes(r.request().method()) && r.ok())
    await card.click()
    await saved
  }
  await page.locator(`[data-test="designer-taskbar"][data-taskbar-style="${style}"]`).waitFor()
}
try {
  await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 15000 })
  await page.locator('[data-test="designer-taskbar"]').waitFor({ timeout: 10000 })
  await themePanel()
  original = await page.locator('[data-test="taskbar-style-card"][aria-checked="true"]').getAttribute('data-style')
  for (const style of ['flowgate', 'modern', 'dock', 'classic']) {
    await choose(style)
    await page.evaluate(() => document.fonts.ready)
    const png = await page.screenshot({ path: join(out, `${style}.png`), fullPage: true })
    if (png.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw new Error('Invalid PNG')
    evidence.captures.push({ style, file: `${style}.png`, sha256: hash(png) })
  }
  if (failures.length) throw new Error('Application errors occurred during capture')
} finally {
  try { if (original) await choose(original) } finally {
    writeFileSync(join(out, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n')
    await browser.close()
  }
}
console.log(`Captured ${evidence.captures.length} real application themes`)
