/**
 * flowgate.default.0604 T0008 §3.5 / D0005 §6 (완료 조건 6) — 승인 대기 화면에서 해결자의
 * supersede 선언이 "실제로 보이는지"를 실제 크롬 + 배포 CSS 번들로 잰다.
 *
 * 좌표·계산 스타일로 판정하는 것:
 *   1) 선언 파일을 고르면 선언 블록이 다이얼로그 안, diff 영역 안에 크기 있게 그려지고,
 *      기존 충돌 표시 태그 바로 아래, diff 줄보다 위에 있다(접히거나 가려지지 않음).
 *   2) 블록에 보존 쪽 문장·사유·대체된 줄 3쌍이 보이고, 대체 전/후 줄의 배경이 서로 다르다.
 *      블록 배경이 투명이 아니다(배포 CSS 가 실제로 적용됨).
 *   3) 파일 목록에서 선언 파일에만 [선언] 표시가 보인다.
 *   4) 선언 없는 파일을 고르면 블록이 없고, 기존 충돌 표시 태그는 그대로다.
 *
 *   cd client && npm run build && node tests/browser/merge-review-supersede.0604.mjs
 *
 * 종료코드 0 = 통과. FLOWGATE_SCRATCH 아래에 채록 JSON 과 스크린샷(PNG)을 남긴다.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const delay = (ms) => new Promise((done) => setTimeout(done, ms))

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run', 'tests/browser/GitMergeReview.supersede.0604.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const assets = await readdir(resolve('dist/assets'))
const cssName = assets.find((n) => n.startsWith('main-') && n.endsWith('.css'))
if (!cssName) throw new Error('production CSS bundle is missing — run `npm run build` first')
// Design tokens live in their own chunk; without it every var() color is transparent.
const tokenCss = await Promise.all(
  assets.filter((n) => n.endsWith('.css') && n !== cssName).map((n) => readFile(resolve('dist/assets', n), 'utf8')),
)
const builtCss = tokenCss.join('\n') + '\n' + await readFile(resolve('dist/assets', cssName), 'utf8')
const builtScope = builtCss.match(/gmr-supersede\[data-v-([a-f0-9]+)\]/)?.[1]
if (!builtScope) throw new Error('the supersede block CSS is missing from the bundle — rebuild after the change')

const states = {}
for (const name of ['declared', 'plain']) {
  const raw = await readFile(resolve(scratch, `merge-review-supersede.${name}.html`), 'utf8')
  const fixtureScope = raw.match(/data-v-([a-f0-9]+)/)?.[1]
  if (!fixtureScope) throw new Error(`fixture ${name} carries no scoped attribute`)
  states[name] = raw.replaceAll('data-v-' + fixtureScope, 'data-v-' + builtScope)
}

const PROBE = String.raw`(() => {
  const dialog = document.querySelector('.gmr-review-dialog');
  if (!dialog) return { error: 'no dialog' };
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const inside = (a, b) => !!(a && b && a.x >= b.x - 1 && a.y >= b.y - 1 && a.x + a.w <= b.x + b.w + 1 && a.y + a.h <= b.y + b.h + 1);
  const visible = (el) => { const b = box(el); const s = el && getComputedStyle(el); return !!(b && b.w > 0 && b.h > 0 && s.visibility !== 'hidden' && s.display !== 'none'); };
  const block = dialog.querySelector('[data-test="gmr-supersede"]');
  const tags = dialog.querySelector('.gmr-origin-tags');
  const diffwrap = dialog.querySelector('.gcd-diffwrap');
  const firstLine = dialog.querySelector('.gcd-line');
  const rows = [...dialog.querySelectorAll('button.gmr-file')];
  const badgeRows = rows.map((row) => { const b = row.querySelector('[data-test="gmr-supersede-badge"]'); return { name: row.querySelector('.gcd-file-name')?.textContent.trim(), badge: !!b, badgeVisible: !!(b && visible(b)), badgeText: b ? b.textContent.trim() : null }; });
  const lines = block ? [...block.querySelectorAll('[data-test="gmr-supersede-lines"] li')] : [];
  const bg = (el) => el ? getComputedStyle(el).backgroundColor : null;
  return {
    hasBlock: !!block,
    blockVisible: !!(block && visible(block)),
    blockBox: box(block),
    blockInDialog: inside(box(block), box(dialog)),
    blockInDiffwrap: inside(box(block), box(diffwrap)),
    blockBelowTags: !!(block && tags && box(block).y >= box(tags).y + box(tags).h - 1),
    blockAboveDiff: !!(block && firstLine && box(block).y + box(block).h <= box(firstLine).y + 1),
    blockInDetails: !!(block && block.closest('details')),
    blockBackground: bg(block),
    sideText: block?.querySelector('[data-test="gmr-supersede-side"]')?.textContent.trim() ?? null,
    reasonText: block?.querySelector('[data-test="gmr-supersede-reason"]')?.textContent.trim() ?? null,
    chunkText: block?.querySelector('.gmr-supersede-chunk-hd')?.textContent.trim() ?? null,
    replacedTitle: block?.querySelector('.gmr-supersede-replaced-hd')?.textContent.trim() ?? null,
    lineCount: lines.length,
    linesVisible: lines.every((li) => visible(li.querySelector('.gmr-supersede-old')) && visible(li.querySelector('.gmr-supersede-new'))),
    oldBackground: bg(lines[0]?.querySelector('.gmr-supersede-old')),
    newBackground: bg(lines[0]?.querySelector('.gmr-supersede-new')),
    originTagCount: dialog.querySelectorAll('.gmr-origin-tag').length,
    badgeRows,
  };
})()`

const profile = resolve(scratch, 'chrome-merge-supersede-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9700 + Math.floor(Math.random() * 200)
const proc = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })

function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  let id = 0
  const pending = new Map()
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data)
    const done = pending.get(msg.id)
    if (done) { pending.delete(msg.id); done(msg) }
  })
  const ready = new Promise((ok, bad) => {
    ws.addEventListener('open', ok, { once: true })
    ws.addEventListener('error', bad, { once: true })
  })
  const call = (method, params = {}) => new Promise((done) => {
    const next = ++id
    pending.set(next, done)
    ws.send(JSON.stringify({ id: next, method, params }))
  })
  return { ws, ready, call }
}

async function newPage() {
  for (let i = 0; i < 80; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
      if (response.ok) return await response.json()
    } catch {}
    await delay(100)
  }
  throw new Error('Chrome connection timeout')
}

async function probe(name, html) {
  const page = await newPage()
  const { ws, ready, call } = connect(page)
  await ready
  await call('Runtime.enable')
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
  const payload = JSON.stringify({ html, css: builtCss })
  await call('Runtime.evaluate', {
    expression: `(() => { const f = ${payload};
      document.head.innerHTML = '<style>' + f.css + '</style>';
      document.body.innerHTML = f.html; })()`,
  })
  await delay(200)
  const out = await call('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  const shot = await call('Page.captureScreenshot', { format: 'png' })
  if (shot.result?.data) {
    await writeFile(resolve(scratch, `merge-review-supersede.${name}.png`), Buffer.from(shot.result.data, 'base64'))
  }
  ws.close()
  if (out.result.exceptionDetails) throw new Error(JSON.stringify(out.result.exceptionDetails))
  return out.result.result.value
}

const TRANSPARENT = new Set(['rgba(0, 0, 0, 0)', 'transparent', null])
const failures = []
try {
  const actual = {}
  for (const [name, html] of Object.entries(states)) actual[name] = await probe(name, html)
  for (const [name, seen] of Object.entries(actual)) {
    if (seen.error) throw new Error(`${name} probe failed: ${seen.error}`)
  }
  const { declared, plain } = actual

  // 1) 블록이 보이는 자리에, 접히지 않고 그려진다.
  if (!declared.hasBlock) failures.push('declared: 선언 블록이 없다')
  if (!declared.blockVisible) failures.push(`declared: 선언 블록이 보이지 않는다 (${JSON.stringify(declared.blockBox)})`)
  if (!declared.blockInDialog) failures.push('declared: 선언 블록이 다이얼로그 밖에 있다')
  if (!declared.blockInDiffwrap) failures.push('declared: 선언 블록이 diff 영역 밖에 있다')
  if (!declared.blockBelowTags) failures.push('declared: 선언 블록이 충돌 표시 태그 아래가 아니다')
  if (!declared.blockAboveDiff) failures.push('declared: 선언 블록이 diff 줄 위가 아니다')
  if (declared.blockInDetails) failures.push('declared: 선언 블록이 접힘(<details>) 안에 있다')
  // 2) 내용과 배포 CSS.
  if (TRANSPARENT.has(declared.blockBackground)) failures.push(`declared: 블록 배경이 투명이다 (${declared.blockBackground})`)
  if (declared.sideText !== '들어오는 쪽이 현재 쪽 변경을 포함한다고 해결자가 선언함') failures.push(`declared.sideText=${declared.sideText}`)
  if (!/^사유: theirs\(0599\)/.test(declared.reasonText || '')) failures.push(`declared.reasonText=${declared.reasonText}`)
  if (!/그대로 남은 줄 151개/.test(declared.chunkText || '')) failures.push(`declared.chunkText=${declared.chunkText}`)
  if (declared.replacedTitle !== '대체된 현재 쪽 줄 (3)') failures.push(`declared.replacedTitle=${declared.replacedTitle}`)
  if (declared.lineCount !== 3) failures.push(`declared.lineCount=${declared.lineCount}`)
  if (!declared.linesVisible) failures.push('declared: 대체 전/후 줄 중 보이지 않는 것이 있다')
  if (declared.oldBackground === declared.newBackground || TRANSPARENT.has(declared.oldBackground)) {
    failures.push(`declared: 대체 전/후 줄 배경이 구분되지 않는다 (${declared.oldBackground} / ${declared.newBackground})`)
  }
  if (declared.originTagCount !== 1) failures.push(`declared.originTagCount=${declared.originTagCount}`)
  // 3) 파일 목록 표시는 선언 파일에만.
  for (const [name, seen] of Object.entries(actual)) {
    const marked = seen.badgeRows.filter((row) => row.badgeVisible).map((row) => row.name)
    if (JSON.stringify(marked) !== JSON.stringify(['test_work_plan_0395.py'])) {
      failures.push(`${name}: 파일 목록 [선언] 표시가 선언 파일에만 있지 않다 (${JSON.stringify(marked)})`)
    }
  }
  // 4) 선언 없는 파일에는 블록이 없고 기존 태그는 그대로다.
  if (plain.hasBlock) failures.push('plain: 선언 없는 파일에 선언 블록이 있다')
  if (plain.originTagCount !== 2) failures.push(`plain.originTagCount=${plain.originTagCount}`)

  await writeFile(
    resolve(scratch, 'merge-review-supersede.0604.json'),
    JSON.stringify({ cssBundle: cssName, actual, failures }, null, 2),
    'utf8',
  )
  console.log(JSON.stringify({ cssBundle: cssName, actual }, null, 2))
} finally {
  if (proc.exitCode === null) proc.kill()
  await delay(300)
  await rm(profile, { recursive: true, force: true })
}
if (failures.length) {
  console.error('SUPERSEDE BLOCK CHECK FAILED:\n' + failures.join('\n'))
  process.exit(1)
}
console.log('SUPERSEDE BLOCK CHECK PASSED')
