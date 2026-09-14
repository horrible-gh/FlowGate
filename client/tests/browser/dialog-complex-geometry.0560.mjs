/**
 * flowgate.default.0560 T0022 — measure the eight migrated instances under the PRODUCTION
 * stylesheet.
 *
 *   npm run build && node tests/browser/dialog-complex-geometry.0560.mjs
 *
 * Needs FLOWGATE_SCRATCH and a fresh `npm run build` (both the widths and the footer row come
 * from CSS that only exists in the bundle). The DOM comes from
 * `DialogComplexGeometry.0560.fixture.spec.ts`, which this script runs first.
 *
 * What only a browser can answer here:
 *
 *   1. WIDTH. §4-1's variant claim is not enough on its own — what a user meets is a number of
 *      pixels. Five of the eight instances do not match a size track exactly, two of them ask
 *      for a size that CHANGES with state (`ai-invoke-loop`, the provider-less proposal
 *      dialog), and two pin their pre-migration width through an unscoped `surface-class`
 *      block. A computed size or an unscoped override that fails to load is invisible to a
 *      DOM-shape test and obvious here.
 *   2. FOOTER ORDER. `DialogFooter` sorts by role before rendering, but the row the user reads
 *      is the painted one, so the order below is read off the buttons' x coordinates rather
 *      than off the DOM.
 *
 * The LAYER CHECKS exist so neither measurement can pass vacuously: if `dialog.css` did not
 * load, every overlay would be static and transparent and every surface unpainted.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run',
  'tests/browser/DialogComplexGeometry.0560.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const cases = JSON.parse(
  await readFile(resolve(scratch, 'dialog-complex-geometry.0560.json'), 'utf8'),
)

const assetNames = await readdir(resolve('dist/assets'))
async function bundle(prefix) {
  const name = assetNames.find((n) => n.startsWith(prefix) && n.endsWith('.css'))
  if (!name) throw new Error(`production CSS bundle ${prefix}*.css is missing — run npm run build`)
  return readFile(resolve('dist/assets', name), 'utf8')
}
/*
 * AppIcon-*       — `shared/app.css` and every design token (surface colours come from here).
 * ConfirmDialog-* — the common dialog layer (`dialog.css`): the size tracks and the footer row.
 * main-*          — the main bundle, including the two unscoped `surface-class` blocks this
 *                   step added (`gcd-changes-dialog`, `gmr-review-dialog`).
 */
const builtCss = [
  await bundle('AppIcon-'),
  await bundle('ConfirmDialog-'),
  await bundle('main-'),
].join('\n')

// Immutable pre-dedup declaration fixture copied from the two SFCs at HEAD before T0026.
// It is intentionally independent of the imported partial so a changed declaration turns this browser gate red.
const PRE_DEDUP_CSS = ".gcd-mono { font-family: var(--mono, ui-monospace, monospace); }\n.gcd-dot { margin: 0 5px; }\n.gcd-retry {\n  flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; padding: 7px 11px;\n  font-size: 0.74rem; border: 1px solid var(--border, #e2e8f0); border-radius: 8px;\n  background: var(--bg, #fff); color: inherit; cursor: pointer;\n}\n.gcd-bd { flex: 1 1 auto; min-height: 0; display: grid; grid-template-columns: minmax(240px, 320px) minmax(0, 1fr); }\n.gcd-filelist { min-height: 0; overflow: auto; padding: 8px; border-right: 1px solid var(--border, #e2e8f0); background: #f8fafc; }\n.gcd-nomatch { margin: 12px 6px; font-size: 0.74rem; color: var(--text-m, #64748b); }\n.gcd-file {\n  width: 100%; display: flex; flex-direction: column; gap: 3px; padding: 8px 9px; margin-bottom: 5px;\n  border: 1px solid transparent; border-radius: 8px; background: transparent; color: inherit; text-align: left; cursor: pointer;\n}\n.gcd-file:hover, .gcd-file.active { border-color: #bfdbfe; background: #fff; }\n.gcd-file-top { display: flex; align-items: center; gap: 6px; min-width: 0; }\n.gcd-file-name { overflow-wrap: anywhere; font: 600 0.75rem var(--mono, ui-monospace, monospace); }\n.gcd-file-dir { font: 0.66rem var(--mono, ui-monospace, monospace); color: var(--text-m, #64748b); overflow-wrap: anywhere; }\n.gcd-badge {\n  flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;\n  width: 16px; height: 16px; border-radius: 4px; font-size: 0.62rem; font-weight: 700;\n}\n.gcd-badge-added { background: var(--success-bg, #dcfce7); color: var(--success, #15803d); }\n.gcd-badge-modified { background: var(--warning-bg, #fef3c7); color: var(--warning, #b45309); }\n.gcd-badge-deleted { background: var(--danger-bg, #fee2e2); color: var(--danger, #b91c1c); }\n.gcd-diffwrap { min-width: 0; min-height: 0; display: flex; flex-direction: column; }\n.gcd-diff-hd { flex: 0 0 auto; display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid var(--border, #e2e8f0); }\n.gcd-diff-path {\n  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;\n  font: 700 0.76rem var(--mono, ui-monospace, monospace);\n}\n.gcd-diff-state { flex: 1 1 auto; display: flex; align-items: center; justify-content: center; gap: 10px; font-size: 0.82rem; color: var(--text-m, #64748b); }\n.gcd-diff-error { flex-direction: column; }\n.gcd-diff { flex: 1 1 auto; min-height: 0; overflow: auto; font: 0.76rem/1.55 var(--mono, ui-monospace, monospace); tab-size: 2; background: #fff; }\n.gcd-gap {\n  padding: 3px 12px; color: var(--text-m, #64748b); background: #f1f5f9;\n  border-top: 1px solid var(--border, #e2e8f0); border-bottom: 1px solid var(--border, #e2e8f0); font-size: 0.71rem;\n}\n.gcd-line { display: grid; grid-template-columns: 46px 46px 14px minmax(0, 1fr); }\n.gcd-ln { padding: 0 6px; text-align: right; color: var(--text-m, #94a3b8); background: #f8fafc; user-select: none; font-size: 0.7rem; }\n.gcd-sign { text-align: center; color: var(--text-m, #94a3b8); }\n.gcd-text { padding: 0 8px; white-space: pre-wrap; overflow-wrap: anywhere; }\n.gcd-line-add, .gcd-text.gcd-line-add { background: #ecfdf5; }\n.gcd-line-del, .gcd-text.gcd-line-del { background: #fef2f2; }\n"
const legacyOverrideCss = PRE_DEDUP_CSS.replace(/(^|})\s*([^@}][^{]*){/g, (_m, close, selectors) =>
  close + selectors.split(',').map((selector) => ':root body ' + selector.trim()).join(', ') + ' {',
)
  + '\n:root body .gmr-bd { grid-template-columns: minmax(200px, 260px) minmax(0, 1fr) minmax(260px, 320px) !important; }'

/**
 * What each instance must measure, and why that number.
 *
 * `width` is the decided target, not always the pre-migration width: the common scale is
 * sm 400 / md 520 / lg 720 / xl 1180, and where an instance's own width was not on it, T0022
 * took the nearest track rather than re-introducing a per-screen pixel value (the habit R0001
 * is about). The two exceptions pin a measured width through `surface-class`, because their
 * app.css track (`.document-modal--edit`, min(1120px, 94vw)) is a shared large-surface
 * decision rather than a per-screen one — the same call T0018 made for `DocumentEditDialog`.
 *
 * `actions` is the left-to-right order DS0007 fixes: 보조/dismiss → 위험 → 실행중단 → 취소 →
 * 주버튼.
 */
const EXPECTED = {
  // 520px `.modal-aiv` → the `md` track exactly.
  'ai-invoke': { width: 520, actions: ['cancel', 'review-start'] },
  // 620px `.modal-aiv--loop` → `lg` (720), the nearest track up.
  'ai-invoke-loop': { width: 720, actions: ['cancel', 'review-start'] },
  // 860px `.modal-cwd` → `xl`; `lg` would squeeze its 5:5 grid.
  'continuous-work': { width: 1180, actions: ['cancel', 'next'] },
  // 940px `.modal-wpc` → `xl`, same reason.
  'work-plan-create': { width: 1180, actions: ['cancel', 'create'] },
  // 1040px `.modal-wpp` → `xl`.
  'work-plan-proposal': {
    width: 1180,
    actions: ['wpp-create-empty', 'wpp-copy-mention', 'wpp-cancel', 'wpp-invoke-ai'],
  },
  // 680px `.modal-nad` → `lg`.
  'next-action': { width: 720, actions: ['cancel', 'proceed'] },
  // 480px `.guc-box` → `md` (520), the step its sibling GitBaseDirtyDialog already took.
  'git-untracked-conflict': { width: 520, actions: ['revert', 'remove', 'cancel', 'commit'] },
  // `.document-modal--edit` min(1120px, 94vw), pinned through `surface-class`.
  'group-changes': { width: 1120, actions: ['back'] },
  'git-merge-review': { width: 1120, actions: ['reject', 'approve'] },
}

const profile = resolve(scratch, 'chrome-complex-geometry-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9600 + Math.floor(Math.random() * 150)
const proc = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*',
  '--window-size=1440,900', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })

const delay = (ms) => new Promise((done) => setTimeout(done, ms))
const failures = []
const report = {}
const builtScopes = {
  'group-changes': builtCss.match(/gcd-modal-body\[data-v-([a-f0-9]+)\]/)?.[1],
  'git-merge-review': builtCss.match(/gmr-conv-log\[data-v-([a-f0-9]+)\]/)?.[1],
}
for (const [name, scope] of Object.entries(builtScopes)) {
  if (!scope) throw new Error(`built scope for ${name} is missing`)
}
function productionScopedHtml(name, html) {
  const marker = name === 'group-changes' ? 'gcd-modal-body' : name === 'git-merge-review' ? 'gmr-conv-log' : null
  if (!marker) return html
  const tag = html.match(new RegExp(`<[^>]*class="[^"]*\\b${marker}\\b[^"]*"[^>]*>`))?.[0]
  const fixtureScope = tag?.match(/data-v-([a-f0-9]+)/)?.[1]
  if (!fixtureScope) throw new Error(`fixture scope for ${name} is missing`)
  return html.replaceAll('data-v-' + fixtureScope, 'data-v-' + builtScopes[name])
}

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
    const payload = JSON.stringify({ html, css: builtCss, legacyCss: legacyOverrideCss })
    /*
     * `.fg-dialog-surface` opens with `animation: mIn .15s` whose first keyframe is
     * `scale(.97)`; measuring synchronously after the innerHTML write reports every surface
     * 3% narrow (720px reads as 698px). Wait for the animations before taking a coordinate.
     */
    const expr = `(async () => {
      const f = ${payload};
      document.head.innerHTML = '<style>html,body{margin:0;padding:0;}</style><style>' + f.css + '</style>';
      document.body.innerHTML = f.html;
      await Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {})));
      await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));

      const round = (n) => Math.round(n);
      const styleProperties = [
        'display', 'flexDirection', 'alignItems', 'justifyContent', 'gap', 'width', 'height',
        'minWidth', 'minHeight', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
        'marginBottom', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
        'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor', 'borderRadius',
        'backgroundColor', 'color', 'fontFamily', 'fontSize', 'fontWeight', 'lineHeight',
        'gridTemplateColumns', 'overflowX', 'overflowY', 'whiteSpace', 'overflowWrap', 'textAlign', 'tabSize',
      ];
      const styleSelectors = [
        '.gcd-bd', '.gcd-filelist', '.gcd-file', '.gcd-file-top', '.gcd-file-name', '.gcd-file-dir',
        '.gcd-badge', '.gcd-badge-modified', '.gcd-diffwrap', '.gcd-diff-hd', '.gcd-diff-path',
        '.gcd-diff', '.gcd-gap', '.gcd-line', '.gcd-ln', '.gcd-sign', '.gcd-text',
        '.gcd-line-add', '.gcd-line-del',
      ];
      const snapshot = () => Object.fromEntries(styleSelectors.map((selector) => {
        const el = document.querySelector(selector);
        if (!el) return [selector, null];
        const style = getComputedStyle(el);
        return [selector, Object.fromEntries(styleProperties.map((property) => [property, style[property]]))];
      }));
      const postDedupStyles = snapshot();
      const baselineStyle = document.createElement('style');
      baselineStyle.textContent = f.legacyCss;
      document.head.appendChild(baselineStyle);
      const preDedupStyles = snapshot();
      baselineStyle.remove();
      const surface = document.querySelector('.fg-dialog-surface');
      const overlay = document.querySelector('.fg-dialog-overlay');
      const footer = document.querySelector('.fg-dialog-footer');
      const buttons = [...document.querySelectorAll('.fg-dialog-footer .fg-dialog-btn')].map((b) => ({
        id: b.getAttribute('data-dialog-action-id'),
        role: b.getAttribute('data-dialog-action-role'),
        left: round(b.getBoundingClientRect().left),
        label: (b.textContent || '').trim().replace(/\s+/g, ' '),
        disabled: b.disabled,
        color: getComputedStyle(b).backgroundColor,
      })).sort((a, b) => a.left - b.left);

      return JSON.stringify({
        viewport: { width: document.documentElement.clientWidth },
        surfaceWidth: surface ? round(surface.getBoundingClientRect().width) : null,
        surfaceHeight: surface ? round(surface.getBoundingClientRect().height) : null,
        surfaceBackground: surface ? getComputedStyle(surface).backgroundColor : null,
        surfaceClasses: surface ? surface.className : null,
        variant: surface ? surface.getAttribute('data-dialog-variant') : null,
        overlayPosition: overlay ? getComputedStyle(overlay).position : null,
        overlayBackground: overlay ? getComputedStyle(overlay).backgroundColor : null,
        overlayZIndex: overlay ? getComputedStyle(overlay).zIndex : null,
        footerTop: footer ? round(footer.getBoundingClientRect().top) : null,
        footerBorderTop: footer ? getComputedStyle(footer).borderTopWidth : null,
        headerCloseCount: document.querySelectorAll('.fg-dialog-header__close').length,
        legacyMarkup: document.querySelectorAll('.modal-bg, .modal-box, .modal-ft, .modal-close').length,
        buttons: buttons,
        computedStyleParity: { preDedup: preDedupStyles, postDedup: postDedupStyles },
      });
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
    const m = await measure(name, productionScopedHtml(name, html))
    const want = EXPECTED[name]
    if (want == null) { failures.push(`${name}: no expectation table entry`); continue }

    /* ── LAYER CHECKS: the stylesheet really loaded, so nothing below is vacuous ── */
    if (m.overlayPosition !== 'fixed') {
      failures.push(`${name}: the overlay is ${m.overlayPosition}, expected fixed — dialog.css did not load`)
    }
    if (m.overlayBackground === 'rgba(0, 0, 0, 0)') {
      failures.push(`${name}: the overlay has no dim — dialog.css did not load`)
    }
    if (m.surfaceBackground === 'rgba(0, 0, 0, 0)') {
      failures.push(`${name}: the surface has no background — the token CSS did not load`)
    }

    /* ── T0026 §4-2: actual Chrome computed values match the pre-dedup declarations ── */
    if (name === 'group-changes' || name === 'git-merge-review') {
      const parity = m.computedStyleParity
      for (const selector of Object.keys(parity.preDedup)) {
        if (parity.preDedup[selector] == null || parity.postDedup[selector] == null) {
          failures.push(`${name}: rendered fixture is missing ${selector}`)
        } else if (JSON.stringify(parity.preDedup[selector]) !== JSON.stringify(parity.postDedup[selector])) {
          failures.push(`${name}: ${selector} computed style changed across the CSS move`)
        }
      }
    }

    /* ── the old shell is really gone from the painted page ── */
    if (m.legacyMarkup !== 0) {
      failures.push(`${name}: ${m.legacyMarkup} legacy .modal-* element(s) are still rendered`)
    }
    if (m.headerCloseCount !== 1) {
      failures.push(`${name}: ${m.headerCloseCount} header close buttons, expected exactly 1`)
    }

    /* ── WIDTH ── */
    if (m.surfaceWidth !== want.width) {
      failures.push(`${name}: the surface is ${m.surfaceWidth}px wide, expected ${want.width}px`)
    }

    /* ── FOOTER ORDER, by coordinate ── */
    const painted = m.buttons.map((b) => b.id)
    if (JSON.stringify(painted) !== JSON.stringify(want.actions)) {
      failures.push(`${name}: the painted footer reads ${JSON.stringify(painted)}, expected ${JSON.stringify(want.actions)}`)
    }
    // Cancel is immediately left of the primary, with nothing between them (DS0007).
    const cancelIndex = m.buttons.findIndex((b) => b.role === 'cancel')
    const primaryIndex = m.buttons.findIndex((b) => b.role === 'primary')
    if (cancelIndex !== -1 && primaryIndex !== -1 && primaryIndex - cancelIndex !== 1) {
      failures.push(`${name}: cancel is not immediately left of the primary (${cancelIndex} → ${primaryIndex})`)
    }
    if (m.buttons.filter((b) => b.role === 'primary').length > 1) {
      failures.push(`${name}: more than one primary button in the footer`)
    }
  }

  /* ── the review dialog's footer band still shows exactly one rule above it ── */
  const review = report['git-merge-review']
  if (review != null && review.footerBorderTop !== '0px') {
    failures.push(`git-merge-review: the footer draws its own top border (${review.footerBorderTop}); the band's single rule belongs to .gmr-ft-extras`)
  }

  await writeFile(
    resolve(scratch, 'dialog-complex-geometry.0560.measured.json'),
    JSON.stringify(report, null, 2), 'utf8',
  )
  console.log(JSON.stringify(
    Object.fromEntries(Object.entries(report).map(([name, m]) => [name, {
      variant: m.variant,
      width: `${m.surfaceWidth}px`,
      expected: EXPECTED[name]?.width ?? null,
      footer: m.buttons.map((b) => `${b.id}(${b.role})`),
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
  'OK — all nine captures render the decided width on the common surface, no legacy .modal-* '
  + 'element survives in the painted page, and every footer row reads in DS0007 order with '
  + 'cancel immediately left of a single primary.',
)
