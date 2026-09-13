/**
 * flowgate.default.0560 T0020 — measure the six split-out dialogs under the PRODUCTION
 * stylesheet, and settle the nested pair's Z-ORDER by hit test rather than by argument.
 *
 *   npm run build && node tests/browser/dialog-nested-split-geometry.0560.mjs
 *
 * Needs FLOWGATE_SCRATCH and a fresh `npm run build` (the widths and the stacking both come
 * from CSS that only exists in the bundle). The DOM comes from
 * `DialogNestedSplitGeometry.0560.fixture.spec.ts`, which this script runs first.
 *
 * Why a browser at all. Two of T0020's claims are painted-pixel claims that jsdom cannot
 * reach, and both are load-bearing:
 *
 *   1. Z-ORDER OF THE NESTED PAIR. §2.2 argues the reject sub-dialog still paints above its
 *      parent after joining the common stack, because `dialogZOrder.base` is 1600 while the
 *      parent's `.modal-bg` is `z-index: 1000` in `shared/app.css` — a comparison between an
 *      inline style and a stylesheet rule that is never loaded in jsdom. Here the stylesheet
 *      is the real one, and the question is asked the way a user asks it: what does the
 *      pointer hit at that coordinate. It is asked three layers deep too, because §2.2 (a)'s
 *      discard confirm opens on top of the sub-dialog.
 *   2. WIDTH PARITY. Every one of the six instances carried a measured width before the
 *      split. The common `size` scale (sm 400 / md 520 / lg 720) covers two of them exactly;
 *      the other four needed a `surface-class` override, and an override that silently fails
 *      to load is exactly the kind of regression a DOM-shape test cannot see.
 *
 * Scoped-CSS hashes are deliberately NOT remapped here (unlike
 * `dialog-local-overlay-geometry.0560.mjs`): neither measurement depends on a scoped rule.
 * Widths come from `dialog.css`'s size tracks and the components' own UNSCOPED
 * `.fg-dialog-surface.<name>` blocks, and stacking comes from `dialog.css` and `app.css`.
 * What would make the run vacuous is those unscoped layers not loading at all, so that is
 * asserted directly (LAYER CHECKS below) instead.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run',
  'tests/browser/DialogNestedSplitGeometry.0560.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const cases = JSON.parse(
  await readFile(resolve(scratch, 'dialog-nested-split-geometry.0560.json'), 'utf8'),
)

const assetNames = await readdir(resolve('dist/assets'))
async function bundle(prefix) {
  const name = assetNames.find((n) => n.startsWith(prefix) && n.endsWith('.css'))
  if (!name) throw new Error(`production CSS bundle ${prefix}*.css is missing — run npm run build`)
  return readFile(resolve('dist/assets', name), 'utf8')
}
/*
 * AppIcon-*       — `shared/app.css` and every design token. `.modal-bg { z-index: 1000 }`,
 *                   the number the whole z-order argument is against, is in here.
 * ConfirmDialog-* — the common dialog layer (`dialog.css`), including the size tracks.
 * main-*          — the main bundle's styles: `gmr-reject-dialog`, `git-panel-dialog`,
 *                   `notif-detail-dialog`.
 * settings-*      — the settings bundle's: `ai-provider-delete-dialog`.
 */
const builtCss = [
  await bundle('AppIcon-'),
  await bundle('ConfirmDialog-'),
  await bundle('main-'),
  await bundle('settings-'),
].join('\n')

/**
 * The width each instance measured BEFORE the split, and where that number came from. The
 * point of the table is that the migration is not allowed to resize anything: T0020 moves
 * instances between files and onto a shared shell, it does not redesign them.
 */
const EXPECTED_WIDTH = {
  // `.modal-box.modal-lg` (app.css) — matched exactly by the `lg` track, no override.
  'ai-provider-form': 720,
  // `.modal-box` (app.css) — matched exactly by the `md` track, no override.
  'ai-provider-command': 520,
  // inline `style="width:440px"` — `confirm-danger`'s `sm` track is 400, so this one needs
  // its `surface-class` override to land.
  'ai-provider-delete': 440,
  // `.git-panel-modal` (0412 T0004) — override.
  'git-status-panel': 620,
  // `.notif-dialog` — override.
  'notification-ai-detail': 640,
  // `.gmr-reject-box`'s `min(480px, calc(100vw - 48px))` — override; at 1440px wide the min
  // resolves to 480.
  'merge-reject-nested': 480,
}

const profile = resolve(scratch, 'chrome-nested-split-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9800 + Math.floor(Math.random() * 150)
const proc = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*',
  '--window-size=1440,900', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })

const delay = (ms) => new Promise((done) => setTimeout(done, ms))
const failures = []
const report = {}

try {
  let page
  for (let i = 0; i < 80; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
      if (response.ok) { page = await response.json(); break }
    } catch { /* chrome not up yet */ }
    await delay(100)
  }
  if (!page) throw new Error('Chrome connection timeout')

  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((ok, bad) => {
    ws.addEventListener('open', ok, { once: true })
    ws.addEventListener('error', bad, { once: true })
  })
  let id = 0
  const pending = new Map()
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data)
    const done = pending.get(msg.id)
    if (done) { pending.delete(msg.id); done(msg) }
  })
  const call = (method, params = {}) => new Promise((done) => {
    const next = ++id
    pending.set(next, done)
    ws.send(JSON.stringify({ id: next, method, params }))
  })
  await call('Runtime.enable')

  async function measure(name, html) {
    const payload = JSON.stringify({ html, css: builtCss })
    /*
     * `.fg-dialog-surface` opens with `animation: mIn .15s` whose first keyframe is
     * `scale(.97)`; measuring synchronously after the innerHTML write reports every surface
     * 3% narrow (720px reads as 698px). Wait for the animations before taking a coordinate.
     *
     * The hit tests use `elementFromPoint`, which answers the question the z-index argument is
     * really about — which layer receives a click at this coordinate — and folds in stacking
     * context, paint order and `pointer-events` the way the browser actually resolves them.
     */
    const expr = `(async () => {
      const f = ${payload};
      document.head.innerHTML = '<style>html,body{margin:0;padding:0;}</style><style>' + f.css + '</style>';
      document.body.innerHTML = f.html;
      await Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {})));
      await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));

      const rect = (el) => { const r = el.getBoundingClientRect(); return {
        left: Math.round(r.left), top: Math.round(r.top),
        right: Math.round(r.right), bottom: Math.round(r.bottom),
        width: Math.round(r.width), height: Math.round(r.height) }; };
      const describe = (el) => el == null ? null : {
        tag: el.tagName.toLowerCase(),
        className: typeof el.className === 'string' ? el.className : '',
        variant: (el.closest('[data-dialog-variant]') || {}).getAttribute
          ? el.closest('[data-dialog-variant]').getAttribute('data-dialog-variant') : null,
        inLegacyParent: !!el.closest('.gmr-modal'),
        inCommonOverlay: !!el.closest('.fg-dialog-overlay'),
      };
      const hit = (x, y) => describe(document.elementFromPoint(Math.round(x), Math.round(y)));

      const overlays = [...document.querySelectorAll('.fg-dialog-overlay')].map((el) => ({
        variant: el.querySelector('[data-dialog-variant]')
          ? el.querySelector('[data-dialog-variant]').getAttribute('data-dialog-variant') : null,
        zIndex: getComputedStyle(el).zIndex,
        position: getComputedStyle(el).position,
        background: getComputedStyle(el).backgroundColor,
        inactive: el.classList.contains('fg-dialog-overlay--inactive'),
        rect: rect(el),
      }));
      const surfaces = [...document.querySelectorAll('.fg-dialog-surface')].map((el) => ({
        variant: el.getAttribute('data-dialog-variant'),
        zIndex: getComputedStyle(el).zIndex,
        background: getComputedStyle(el).backgroundColor,
        inert: el.hasAttribute('inert'),
        rect: rect(el),
      }));

      const legacyBg = document.querySelector('.modal-bg');
      const legacyBox = document.querySelector('.gmr-modal');
      // The parent controls T0020 §2.2 disables by hand; their painted state is what a user
      // meets, so the hit test over one of them is the real "부모 조작 불가" check.
      const parentApprove = document.querySelector('.gmr-ft-actions .btn-primary');

      const out = {
        viewport: {
          width: document.documentElement.clientWidth,
          height: document.documentElement.clientHeight,
        },
        overlays: overlays,
        surfaces: surfaces,
        legacy: legacyBg ? {
          bgZIndex: getComputedStyle(legacyBg).zIndex,
          bgPosition: getComputedStyle(legacyBg).position,
          bgRect: rect(legacyBg),
          boxRect: legacyBox ? rect(legacyBox) : null,
          boxBackground: legacyBox ? getComputedStyle(legacyBox).backgroundColor : null,
        } : null,
        hits: {},
      };

      // Topmost common surface = the last one in the host (the stack appends), which is also
      // the one that is not inert.
      const top = surfaces.length > 0 ? surfaces[surfaces.length - 1] : null;
      if (top) {
        out.hits.topSurfaceCentre = hit((top.rect.left + top.rect.right) / 2, (top.rect.top + top.rect.bottom) / 2);
        // A coordinate 6px outside the top surface but still inside its overlay: the dim.
        out.hits.justOutsideTopSurface = hit((top.rect.left + top.rect.right) / 2, top.rect.top - 6);
      }
      if (surfaces.length > 1) {
        const under = surfaces[surfaces.length - 2];
        out.hits.underSurfaceCentre = hit((under.rect.left + under.rect.right) / 2, (under.rect.top + under.rect.bottom) / 2);
      }
      if (legacyBox) {
        // Top-left corner of the legacy parent box, well away from any common surface.
        out.hits.legacyBoxCorner = hit(legacyBox.getBoundingClientRect().left + 8, legacyBox.getBoundingClientRect().top + 8);
      }
      if (parentApprove) {
        const r = parentApprove.getBoundingClientRect();
        out.hits.parentApprove = hit((r.left + r.right) / 2, (r.top + r.bottom) / 2);
        out.parentApproveDisabled = parentApprove.disabled;
      }
      return JSON.stringify(out);
    })()`
    const result = await call('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
    if (result.result?.exceptionDetails) {
      throw new Error(`${name}: ${JSON.stringify(result.result.exceptionDetails)}`)
    }
    const measured = JSON.parse(result.result.result.value)
    report[name] = measured
    return measured
  }

  for (const [name, html] of Object.entries(cases)) {
    const m = await measure(name, html)

    /* ── LAYER CHECKS: the stylesheet really loaded, so nothing below is vacuous ── */
    if (m.overlays.length === 0) { failures.push(`${name}: no common overlay rendered`); continue }
    for (const overlay of m.overlays) {
      if (overlay.position !== 'fixed') {
        failures.push(`${name}: an overlay is ${overlay.position}, expected fixed — dialog.css did not load`)
      }
      if (overlay.background === 'rgba(0, 0, 0, 0)') {
        failures.push(`${name}: an overlay has no dim — dialog.css did not load`)
      }
    }
    for (const s of m.surfaces) {
      if (s.background === 'rgba(0, 0, 0, 0)') {
        failures.push(`${name}: the ${s.variant} surface has no background — the token CSS did not load`)
      }
      if (s.rect.width <= 0 || s.rect.height <= 0) {
        failures.push(`${name}: the ${s.variant} surface has no box`)
      }
    }

    /* ── WIDTH PARITY ── */
    const expected = EXPECTED_WIDTH[name]
    if (expected != null) {
      // For the nested case the measured surface is the reject sub-dialog (the only common
      // surface on screen); the legacy parent is not part of this T's size contract.
      const actual = m.surfaces[m.surfaces.length - 1].rect.width
      if (actual !== expected) {
        failures.push(`${name}: the surface is ${actual}px wide, expected ${expected}px (the pre-split width)`)
      }
    }

    /* ── Z-ORDER, the nested pair ── */
    if (m.legacy != null) {
      if (m.legacy.bgZIndex !== '1000') {
        failures.push(`${name}: the legacy parent's .modal-bg is z-index ${m.legacy.bgZIndex}, expected 1000 (app.css) — the premise of §2.2 does not hold`)
      }
      if (m.legacy.boxRect == null || m.legacy.boxRect.width <= 0) {
        failures.push(`${name}: the legacy parent box did not render, so there is nothing to stack above`)
      }
      // The child really is the layer a pointer meets over its own surface...
      const top = m.hits.topSurfaceCentre
      if (top == null || !top.inCommonOverlay || top.inLegacyParent) {
        failures.push(`${name}: the top common surface is not what the pointer hits at its centre — got ${JSON.stringify(top)}`)
      }
      // ...and its dim covers the parent, which is what makes the parent unreachable.
      const overParent = m.hits.legacyBoxCorner
      if (overParent == null || !overParent.inCommonOverlay) {
        failures.push(`${name}: a click on the legacy parent box reaches the parent — the child's dim is not above it (got ${JSON.stringify(overParent)})`)
      }
      // §2.2's hand-written guard, in its painted state.
      if (m.parentApproveDisabled !== true) {
        failures.push(`${name}: the parent's [승인] is not disabled while the child is open`)
      }
      if (m.hits.parentApprove != null && !m.hits.parentApprove.inCommonOverlay) {
        failures.push(`${name}: the parent's [승인] is reachable by pointer while the child is open`)
      }
    }

    /* ── Z-ORDER, three layers deep (§2.2 (a)'s discard confirm) ── */
    if (m.overlays.length > 1) {
      const zIndexes = m.overlays.map((o) => Number(o.zIndex))
      for (let i = 1; i < zIndexes.length; i++) {
        if (!(zIndexes[i] > zIndexes[i - 1])) {
          failures.push(`${name}: overlay ${i} (z ${zIndexes[i]}) is not above overlay ${i - 1} (z ${zIndexes[i - 1]})`)
        }
      }
      // The dialog underneath is kept but taken out of reach — L0009 §2's "child active 중
      // parent" for two members that ARE both on the stack.
      const under = m.surfaces[m.surfaces.length - 2]
      if (under != null && under.inert !== true) {
        failures.push(`${name}: the ${under.variant} surface under the top one is not inert`)
      }
      const hitUnder = m.hits.underSurfaceCentre
      if (hitUnder != null && hitUnder.variant === under?.variant) {
        failures.push(`${name}: a click at the centre of the ${under.variant} surface still reaches it while a dialog is open above`)
      }
    }
  }

  await writeFile(
    resolve(scratch, 'dialog-nested-split-geometry.0560.measured.json'),
    JSON.stringify(report, null, 2), 'utf8',
  )
  console.log(JSON.stringify(
    Object.fromEntries(Object.entries(report).map(([name, m]) => [name, {
      width: m.surfaces.map((s) => `${s.variant}=${s.rect.width}px`),
      expectedWidth: EXPECTED_WIDTH[name] ?? null,
      overlayZ: m.overlays.map((o) => `${o.variant ?? '?'}@${o.zIndex}`),
      legacyParentZ: m.legacy?.bgZIndex ?? null,
      hitAtTopSurface: m.hits.topSurfaceCentre?.variant ?? null,
      hitOverLegacyParent: m.hits.legacyBoxCorner?.inCommonOverlay ?? null,
      parentApproveDisabled: m.parentApproveDisabled ?? null,
      inert: m.surfaces.map((s) => `${s.variant}=${s.inert}`),
    }])),
    null, 2,
  ))
} finally {
  proc.kill()
}

if (failures.length > 0) {
  console.error('FAILURES:\n' + failures.map((f) => ' - ' + f).join('\n'))
  process.exit(1)
}
console.log(
  'OK — all six split-out dialogs keep their pre-split width, the reject sub-dialog paints and '
  + 'takes the pointer above its legacy parent (1600 vs app.css 1000), and the discard confirm '
  + 'stacks one step above the sub-dialog, which goes inert underneath it.',
)
