// flowgate.v02test.0002 R0001 #2 (0010-T §5) - browser measurement harness for the Git box. Not part of the product
// build and not a vitest spec: measure.mjs (same folder) bundles this file and opens it in a headless Chromium.
// It mounts the real CheckSettings.vue with a fixture view (the component's own `view` prop, no server), below the
// approved mockup (flowgate_git_settings_mockup.html) in an iframe, and writes DOM measurements into #fg-measure:
// rects relative to each box, computed font sizes, pairwise overlaps of the controls, controls outside the box content.
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { installInspectorMessages } from '@/features/designer/inspector/inspectorRegistry'
import '@/app/styles/theme.css'
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'

const q = new URLSearchParams(location.search)
const BOX = Number(q.get('box') ?? '910')
const VIEW = Number(q.get('mock') ?? '1080')

registerAppMessages(i18n)
installInspectorMessages(i18n.global as never)
;(i18n.global.locale as unknown as { value: string }).value = 'ko'

const target = {
  projectId: 'flowgate', flowId: 'flowgate.v02test.0002', nodeId: 'harness_check', nodeKind: 'approval', docId: null,
  docType: null, cardKind: 'check', title: '확인', canvasState: null, docReviewStatus: null, docStatus: null,
  supersedes: [], slot: null, nodeRun: null, cardDocuments: [], nodeType: 'gate', readOnly: null,
} as never
const base = { usage: null, archive: false, scope: 'merge', target: 'feature/v0.2.0', targetStored: false,
  defaultTarget: 'feature/v0.2.0', push: true, keepBranch: false, connected: true, reason: null, failures: {} }
const STATES: Record<string, unknown> = {
  timeout: { ...base, targetEnabled: false, targetOptions: [], lookupError: 'target.lookup_timeout', canRefreshTargets: true },
  ok: { ...base, targetEnabled: true, targetOptions: ['feature/v0.2.0', 'main', 'release/local'], lookupError: null, canRefreshTargets: false },
}

document.getElementById('mock-cap')!.textContent = `승인 시안 flowgate_git_settings_mockup.html — viewport ${VIEW}px`
document.getElementById('real-cap')!.textContent = `현재 구현 CheckSettings.vue — Git 박스 바깥 폭 ${BOX}px (위: 조회 시간 초과 상태, 아래: 조회 성공 상태)`
const frame = document.getElementById('mockframe') as HTMLIFrameElement
frame.style.width = `${VIEW}px`
for (const [name, view] of Object.entries(STATES)) {
  const host = document.createElement('div')
  host.className = 'real-host'
  host.dataset.state = name
  host.style.width = `${BOX}px`
  document.getElementById('real')!.appendChild(host)
  const app = createApp({ render: () => h(CheckSettings as never, { target, view }) })
  app.use(createPinia())
  app.use(i18n)
  app.mount(host)
}

type R = { x: number; y: number; w: number; h: number }
const r2 = (n: number) => Math.round(n * 100) / 100
function rel(el: Element | null, origin: DOMRect): R | null {
  if (!el) return null
  const r = el.getBoundingClientRect()
  return { x: r2(r.left - origin.left), y: r2(r.top - origin.top), w: r2(r.width), h: r2(r.height) }
}
function css(el: Element | null, ...props: string[]) {
  if (!el) return null
  const s = getComputedStyle(el)
  return Object.fromEntries(props.map(p => [p, s.getPropertyValue(p)]))
}
function overlaps(named: Record<string, R | null>) {
  const out: string[] = []
  const keys = Object.keys(named).filter(k => named[k] && named[k]!.w > 0 && named[k]!.h > 0)
  for (let i = 0; i < keys.length; i++) for (let j = i + 1; j < keys.length; j++) {
    const a = named[keys[i]]!, b = named[keys[j]]!
    const ix = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
    const iy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
    if (ix > 0.5 && iy > 0.5) out.push(`${keys[i]}x${keys[j]} (${r2(ix)}x${r2(iy)})`)
  }
  return out
}
function outside(named: Record<string, R | null>, box: Element) {
  const s = getComputedStyle(box)
  const b = box.getBoundingClientRect()
  const left = parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft)
  const right = b.width - parseFloat(s.borderRightWidth) - parseFloat(s.paddingRight)
  return Object.entries(named)
    .filter(([, r]) => r && (r.x < left - 0.5 || r.x + r.w > right + 0.5))
    .map(([k, r]) => `${k} x=${r!.x}..${r2(r!.x + r!.w)} content=${left}..${right}`)
}
function layout(p: Record<string, R | null>) {
  const s1 = p.switch1, s2 = p.switch2, sel = p.select
  const ref = p.refresh ?? p.select
  return {
    switchesBesideTarget: !!(s1 && sel && ref && s1.x >= ref.x + ref.w - 0.5 && s1.y < sel.y + sel.h),
    switchesSideBySide: !!(s1 && s2 && Math.abs(s1.y - s2.y) < 2 && s2.x >= s1.x + s1.w),
    switchOrder: s1 && s2 ? (s1.y < s2.y - 2 || (Math.abs(s1.y - s2.y) < 2 && s1.x < s2.x) ? 'push>keep' : 'keep>push') : null,
    errorUnderSelect: !!(p.error && sel && p.error.y >= sel.y + sel.h - 0.5),
    errorGapToSelect: p.error && sel ? r2(p.error.y - (sel.y + sel.h)) : null,
    refreshGapToSelect: p.refresh && sel ? r2(p.refresh.x - (sel.x + sel.w)) : null,
    switchGap: s1 && s2 && Math.abs(s1.y - s2.y) < 2 ? r2(s2.x - (s1.x + s1.w)) : null,
  }
}
function measureBox(box: Element, sel: Record<string, string | [string, number]>) {
  const origin = box.getBoundingClientRect()
  const pick = (s: string | [string, number]) => (Array.isArray(s) ? box.querySelectorAll(s[0])[s[1]] ?? null : box.querySelector(s))
  const parts: Record<string, R | null> = {}
  for (const [k, s] of Object.entries(sel)) parts[k] = rel(pick(s), origin)
  const bs = getComputedStyle(box)
  return {
    box: { w: r2(origin.width), h: r2(origin.height), padding: bs.padding, radius: bs.borderRadius,
      contentWidth: r2(box.clientWidth - parseFloat(bs.paddingLeft) - parseFloat(bs.paddingRight)) },
    parts,
    styles: {
      title: css(pick(sel.title), 'font-size', 'font-weight', 'line-height', 'padding-bottom', 'border-bottom-width', 'margin-bottom'),
      label1: css(pick(sel.label1), 'font-size', 'font-weight', 'color'),
      segmentOption: css(pick(sel.segmentOption), 'font-size', 'font-weight'),
      select: css(pick(sel.select), 'font-size', 'border-radius'),
      error: css(pick(sel.error), 'font-size', 'font-weight', 'line-height', 'color'),
      switchLabel: css(pick(sel.switchLabel), 'font-size', 'color'),
    },
    overlaps: overlaps(Object.fromEntries(['label1', 'segment', 'label2', 'select', 'refresh', 'error', 'switch1', 'switch2'].map(k => [k, parts[k]]))),
    outsideContent: outside(Object.fromEntries(['segment', 'select', 'refresh', 'error', 'switch1', 'switch2'].map(k => [k, parts[k]])), box),
    layout: layout(parts),
  }
}
const REAL_SEL: Record<string, string | [string, number]> = {
  title: '.inspector-block-head', row1: ['[data-test="inspector-control-row"]', 0], row2: ['[data-test="inspector-control-row"]', 1],
  label1: ['.inspector-control-label', 0], label2: ['.inspector-control-label', 1], segment: '[data-test="check-settings-scope"]',
  segmentOption: '.inspector-segment-option', select: '[data-test="check-settings-target"]', refresh: '[data-test="check-settings-refresh"]',
  refreshIcon: '[data-test="check-settings-refresh"] svg', error: '.inspector-check-settings-target-help', switches: '[data-test="check-settings-switches"]',
  switch1: ['.inspector-check-settings-toggle', 0], switch2: ['.inspector-check-settings-toggle', 1], switchLabel: '.inspector-check-settings-toggle > span',
  toggle1: '[data-test="check-settings-push"]', toggle2: '[data-test="check-settings-keep-branch"]',
}
const MOCK_SEL: Record<string, string | [string, number]> = {
  title: '.panel-title', row1: '.segmented', row2: '.branch-area', label1: ['.form-label', 0], label2: ['.form-label', 1], segment: '.segmented',
  segmentOption: '.mode', select: 'select', refresh: '.refresh', refreshIcon: '.refresh svg', error: '.error', switches: '.settings',
  switch1: ['.switch-pair', 0], switch2: ['.switch-pair', 1], switchLabel: '.switch-label', toggle1: ['.toggle', 0], toggle2: ['.toggle', 1],
}

async function run() {
  await nextTick()
  await document.fonts.ready
  await new Promise<void>(res => (frame.contentDocument?.readyState === 'complete' && frame.contentDocument.querySelector('.panel') ? res() : frame.addEventListener('load', () => res(), { once: true })))
  const md = frame.contentDocument!
  frame.style.height = `${md.documentElement.scrollHeight}px`
  await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)))
  const result: Record<string, unknown> = { box: BOX, mockViewport: VIEW, ua: navigator.userAgent, dpr: devicePixelRatio,
    fonts: { real: getComputedStyle(document.querySelector('.real-host')!).fontFamily, mock: getComputedStyle(md.body).fontFamily } }
  const panel = md.querySelector('.panel')
  result.mock = panel ? measureBox(panel, MOCK_SEL) : { error: `no mock panel (readyState=${md.readyState}, url=${md.URL})` }
  for (const host of document.querySelectorAll<HTMLElement>('.real-host')) {
    const box = host.querySelector('[data-test="check-settings-git"]')
    result[`real_${host.dataset.state}`] = box ? measureBox(box, REAL_SEL) : { error: 'no git box' }
    if (box) result[`attrs_${host.dataset.state}`] = Array.from(box.attributes).map(a => `${a.name}=${a.value}`)
  }
  result.pageHeight = document.documentElement.scrollHeight
  document.getElementById('fg-measure')!.textContent = `FGMEASURE${JSON.stringify(result)}FGMEASURE`
  document.title = 'FG_DONE'
}
run().catch(e => {
  document.getElementById('fg-measure')!.textContent = `FGMEASURE${JSON.stringify({ error: String((e as Error)?.stack ?? e) })}FGMEASURE`
})
