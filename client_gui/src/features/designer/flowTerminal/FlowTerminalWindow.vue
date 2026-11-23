<template>
  <Teleport to="body">
    <section v-show="visible && !docked" ref="root" class="ft-window" :class="{ focused, minimized }" :style="placeStyle"
      tabindex="-1" role="dialog" :aria-label="`${w.title} · ${title}`" data-test="flow-terminal-window" :data-flow-id="flowId"
      :data-minimized="minimized ? 'true' : undefined" @pointerdown="emit('focus')">
      <header class="ft-head" @pointerdown="startDrag" @dblclick="toggleMinimize">
        <span class="ft-head-square" aria-hidden="true">&gt;_</span>
        <span class="ft-title">{{ w.title }}</span>
        <span class="ft-flow" :title="title">{{ title }}</span>
        <!-- 0001-R §3.1-6: the working folder is always in the title bar (a long path is cut at its start, the tooltip has all). -->
        <span class="ft-cwd" :title="`${w.cwd}: ${session.cwd}`" data-test="flow-terminal-cwd"><bdi dir="ltr">{{ session.cwd }}</bdi></span>
        <span class="ft-spacer" />
        <!-- 0009-T 할 일 1: into the Designer's bottom Dock (the same shell goes on there). -->
        <Button variant="ghost" size="sm" class="ft-head-btn" :title="w.dock" :aria-label="w.dock" :disabled="!canDock"
          data-test="flow-terminal-dock" @pointerdown.stop @dblclick.stop @click="emit('dock', true)">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11v9h-11z" /><path d="M2.5 9h11" /><path d="M8 4.8v2.6M6.6 6l1.4 1.4L9.4 6" /></svg>
        </Button>
        <Button variant="ghost" size="sm" class="ft-head-btn" :title="minimized ? w.restore : w.minimize" :aria-label="minimized ? w.restore : w.minimize"
          data-test="flow-terminal-minimize" @pointerdown.stop @dblclick.stop @click="toggleMinimize">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path v-if="minimized" d="M3.5 3.5h9v9h-9z" /><path v-else d="M3.5 11.5h9" /></svg>
        </Button>
        <Button variant="ghost" size="sm" class="ft-head-btn ft-close" :title="w.close" :aria-label="w.close" data-test="flow-terminal-close"
          @pointerdown.stop @dblclick.stop @click="requestClose">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </Button>
      </header>

      <div v-show="!minimized" class="ft-main">
        <!-- 0009-T 할 일 2·4: docked, this part (the xterm.js screen, its overlays and the band) is moved into the bottom
             Dock's terminal place by the Teleport — the same DOM, Terminal, WebSocket and session; nothing is made again. -->
        <Teleport :to="dockTarget ?? 'body'" :disabled="!docked || !dockTarget">
        <div v-show="!docked || visible" class="ft-body" :class="{ 'is-docked': docked }" data-test="flow-terminal-body"
          :data-flow-id="flowId" :data-docked="docked ? 'true' : undefined" @pointerdown="docked && emit('focus')">
        <header v-if="docked" class="ft-dock-head" data-test="flow-terminal-dock-head">
          <span class="ft-dock-label">{{ w.cwd }}</span>
          <span class="ft-cwd ft-dock-cwd" :title="`${w.cwd}: ${session.cwd}`" data-test="flow-terminal-dock-cwd"><bdi dir="ltr">{{ session.cwd }}</bdi></span>
          <span class="ft-spacer" />
          <Button variant="ghost" size="sm" class="ft-dock-btn" :title="w.undock" data-test="flow-terminal-undock" @click="emit('dock', false)">{{ w.undock }}</Button>
          <Button variant="ghost" size="sm" class="ft-dock-btn ft-close" :title="w.close" data-test="flow-terminal-dock-close" @click="requestClose">{{ w.dockClose }}</Button>
        </header>
        <p v-if="cardRunning && !ended" class="ft-band" role="status" data-test="flow-terminal-card-running">{{ w.cardRunning }}</p>
        <div class="ft-screen-wrap">
          <div ref="screen" class="ft-screen" data-test="flow-terminal-screen" />
          <p v-if="!connected && !ended" class="ft-connecting" data-test="flow-terminal-connecting">{{ w.connecting }}</p>
          <div v-if="ended" class="ft-ended" role="status" data-test="flow-terminal-ended" :data-reason="ended.reason">
            <strong>{{ w.ended }}</strong>
            <small>{{ endedDetail }}</small>
            <Button variant="primary" size="sm" data-test="flow-terminal-reopen" @click="emit('reopen')">{{ w.reopen }}</Button>
          </div>
          <div v-if="confirming" class="ft-confirm" role="alertdialog" :aria-label="w.confirmTitle" data-test="flow-terminal-confirm">
            <div class="ft-confirm-card">
              <h3>{{ w.confirmTitle }}</h3>
              <p>{{ w.confirmBody }}</p>
              <div class="ft-confirm-actions">
                <Button variant="secondary" size="sm" data-test="flow-terminal-confirm-cancel" @click="confirming = false">{{ w.cancel }}</Button>
                <Button variant="danger" size="sm" data-test="flow-terminal-confirm-close" @click="closeNow">{{ w.confirmClose }}</Button>
              </div>
            </div>
          </div>
        </div>
        </div>
        </Teleport>
      </div>
    </section>
  </Teleport>
</template>

<script setup lang="ts">
// One floating terminal window = one server session (0078 · 0001-R §3.1·§3.4, 0005-T 할 일 3·4). The window connects
// to WS /terminal/ws with the one-time ticket at once (it is valid for 30 s), draws the shell with xterm.js (loaded
// only when a terminal opens) and sends its rows / columns whenever its size changes. Closing the window ends the
// session (DELETE /sessions/{id}); a running command (busy) asks once first. A connection that drops shows
// "세션이 끝났습니다" with [새로 열기] — no reattach in this stage. Hidden (another Flow tab) the session goes on.
// Copy: Ctrl+Shift+C, or Ctrl+C while text is selected (Ctrl+C without a selection is the shell's interrupt).
// Paste: Ctrl+V / Ctrl+Shift+V go to the browser's paste, which xterm.js sends as input. Korean input uses the IME.
// 0009-T: `docked` draws the screen in the Designer's bottom Dock (`dockTarget`) instead of the window — [아래에 붙이기]
// / [떼기] ask the Host (`dock` event). The window keeps its place and size while docked, so [떼기] brings it back
// where it was. After each move the screen is fitted again and its rows / columns are sent to the shell; the Dock's
// height and the Center column's width are followed by the same ResizeObserver as the window's size.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Terminal } from '@xterm/xterm'
import type { FitAddon } from '@xterm/addon-fit'
import Button from '@/components/ui/Button.vue'
import { closeTerminalSession, terminalSocketUrl, type TerminalSession } from './api'
import { inputFrame, parseServerMessage, resizeFrame, type ExitReason, type ServerMessage } from './protocol'
import { initialTerminalRect } from './placement'
import { TERMINAL_FONT, TERMINAL_THEME } from './theme'
import { wordsFor } from './words'

const props = defineProps<{
  flowId: string; title: string; session: TerminalSession; position: number
  visible: boolean; focused: boolean; zIndex: number
  /** Drawn in the bottom Dock (0009-T). */
  docked?: boolean
  /** The bottom Dock's terminal place; null = no Dock to go to ([아래에 붙이기] is unavailable). */
  dockTarget?: HTMLElement | null
}>()
const emit = defineEmits<{ close: []; focus: []; reopen: []; dock: [docked: boolean] }>()
const { locale } = useI18n()
const w = computed(() => wordsFor(locale.value))
const canDock = computed(() => !!props.dockTarget)

/* ── place: dragged by the header, resized by the corner (CSS resize), minimized to the header ── */
const root = ref<HTMLElement | null>(null)
const screen = ref<HTMLElement | null>(null)
const place = reactive({ x: 0, y: 0 })
const placeStyle = computed(() => ({ left: `${place.x}px`, top: `${place.y}px`, zIndex: props.zIndex }))
const first = initialTerminalRect(innerWidth, innerHeight, props.position)
place.x = first.x; place.y = first.y
const minimized = ref(false)
let drag: { sx: number; sy: number; x: number; y: number } | null = null
function startDrag(event: PointerEvent) {
  if ((event.target as Element).closest('button')) return
  drag = { sx: event.clientX, sy: event.clientY, x: place.x, y: place.y }
  document.addEventListener('pointermove', moveDrag)
  document.addEventListener('pointerup', endDrag)
}
function moveDrag(event: PointerEvent) {
  if (!drag || !root.value) return
  place.x = Math.max(0, Math.min(innerWidth - root.value.offsetWidth, drag.x + event.clientX - drag.sx))
  place.y = Math.max(0, Math.min(innerHeight - 45, drag.y + event.clientY - drag.sy))
}
function endDrag() {
  drag = null
  document.removeEventListener('pointermove', moveDrag)
  document.removeEventListener('pointerup', endDrag)
}
function keepInView() {
  if (!root.value) return
  place.x = Math.min(place.x, Math.max(0, innerWidth - root.value.offsetWidth))
  place.y = Math.min(place.y, Math.max(0, innerHeight - 45))
}
function toggleMinimize() {
  minimized.value = !minimized.value
  if (!minimized.value) void nextTick(() => { fitNow(); term?.focus() })
}
/** Brings the window back (from minimized too) and puts the keyboard in the shell. */
function reveal() {
  if (minimized.value && !props.docked) toggleMinimize()
  if (term && !ended.value) term.focus()
  else root.value?.focus({ preventScroll: true })
}

/* ── the session ── */
const connected = ref(false)
const busy = ref(false)
const cardRunning = ref(false)
const confirming = ref(false)
const ended = ref<{ reason: ExitReason; code: number | null } | null>(null)
const endedDetail = computed(() => {
  const end = ended.value
  if (!end) return ''
  return end.code === null ? w.value.exit[end.reason] : `${w.value.exit[end.reason]} · ${w.value.exitCode(end.code)}`
})
let socket: WebSocket | null = null
let term: Terminal | null = null
let fit: FitAddon | null = null
let observer: ResizeObserver | null = null
let disposed = false
const early: string[] = [] // output that came before the screen was ready

function send(frame: string) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(frame)
}
function connect() {
  let ws: WebSocket
  try {
    ws = new WebSocket(terminalSocketUrl(props.session.ticket))
  } catch {
    finish('error', null)
    return
  }
  socket = ws
  ws.onopen = () => {
    connected.value = true
    if (term) send(resizeFrame(term.cols, term.rows))
  }
  ws.onmessage = event => handle(parseServerMessage(event.data))
  ws.onclose = () => {
    if (socket === ws) socket = null
    if (!disposed) finish('closed', null)
  }
}
function handle(message: ServerMessage | null) {
  if (!message || ended.value) return
  if (message.type === 'output') { if (term) term.write(message.data); else early.push(message.data) }
  else if (message.type === 'busy') busy.value = message.value
  else if (message.type === 'card_running') cardRunning.value = message.value
  else finish(message.reason, message.code)
}
function finish(reason: ExitReason, code: number | null) {
  if (ended.value) return
  ended.value = { reason, code }
  busy.value = false
  confirming.value = false
  const ws = socket
  socket = null
  ws?.close()
}

async function loadScreen() {
  const [{ Terminal: TerminalClass }, { FitAddon: FitClass }] = await Promise.all([
    import('@xterm/xterm'), import('@xterm/addon-fit'), import('@xterm/xterm/css/xterm.css'),
  ])
  if (disposed || !screen.value) return
  const created = new TerminalClass({
    cursorBlink: true, fontFamily: TERMINAL_FONT, fontSize: 13, lineHeight: 1.15, scrollback: 5000,
    theme: TERMINAL_THEME, allowProposedApi: false,
  })
  fit = new FitClass()
  created.loadAddon(fit)
  created.open(screen.value)
  created.attachCustomKeyEventHandler(keyEvent)
  created.onData(data => send(inputFrame(data)))
  created.onResize(({ cols, rows }) => send(resizeFrame(cols, rows)))
  term = created
  fitNow()
  for (const chunk of early.splice(0)) created.write(chunk)
  send(resizeFrame(created.cols, created.rows))
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(() => fitNow())
    observer.observe(screen.value)
  }
  if (props.visible && props.focused) created.focus()
}
function fitNow() {
  if (!fit || (minimized.value && !props.docked) || !props.visible || !screen.value?.offsetWidth) return
  try { fit.fit() } catch { /* not measurable yet (hidden) — the next resize fits */ }
}
function keyEvent(event: KeyboardEvent): boolean {
  if (event.type !== 'keydown') return true
  const key = event.key.toLowerCase()
  const mod = event.ctrlKey || event.metaKey
  if (mod && key === 'c' && (event.shiftKey || event.metaKey || term?.hasSelection())) {
    const text = term?.getSelection() ?? ''
    if (text) void navigator.clipboard?.writeText(text).catch(() => {})
    if (!event.metaKey) term?.clearSelection()
    event.preventDefault()
    return false
  }
  // Leave Ctrl+V to the browser: its paste event reaches xterm.js, which sends the text (bracketed paste aware).
  if (mod && key === 'v') return false
  return true
}

/* ── close ── */
function requestClose() {
  if (busy.value && !ended.value) { confirming.value = true; return }
  closeNow()
}
function closeNow() {
  shutdown()
  emit('close')
}
function shutdown() {
  if (disposed) return
  disposed = true
  const live = !ended.value
  const ws = socket
  socket = null
  ws?.close()
  if (live) void closeTerminalSession(props.session.session_id).catch(() => {})
  observer?.disconnect()
  observer = null
  term?.dispose()
  term = null
  fit = null
}

watch(() => props.visible, shown => { if (shown) void nextTick(fitNow) })
// Moved into / out of the bottom Dock: the window comes back unminimized, the screen is fitted to its new place and
// the shell gets the rows / columns (sent again even when they happen to be the same).
watch(() => props.docked && !!props.dockTarget, async () => {
  if (props.docked) minimized.value = false
  await nextTick()
  fitNow()
  if (term) send(resizeFrame(term.cols, term.rows))
})
onMounted(() => {
  const el = root.value
  if (el) { el.style.width = `${first.width}px`; el.style.height = `${first.height}px` }
  connect()
  void loadScreen().catch(() => finish('error', null))
  addEventListener('resize', keepInView)
})
onBeforeUnmount(() => {
  endDrag()
  removeEventListener('resize', keepInView)
  shutdown()
})
defineExpose({ reveal, requestClose })
</script>

<style scoped>
.ft-window { position: fixed; min-width: 420px; min-height: 240px; max-width: calc(100vw - 18px); max-height: calc(100vh - 12px);
  display: flex; flex-direction: column; overflow: hidden; resize: both; box-sizing: border-box; outline: none;
  border: 1px solid #4a88bf; border-radius: 9px; background: #0a1626; color: #d8e8f5; font-size: 12px;
  box-shadow: 0 18px 58px rgba(0, 0, 0, .62), 0 0 0 1px rgba(12, 33, 49, .6); transition: box-shadow .18s; }
.ft-window.focused { border-color: #66b6fc; box-shadow: 0 21px 65px rgba(0, 0, 0, .75), 0 0 0 1px #265a85; }
/* Minimized: only the title bar stays (the size it had comes back on restore). */
.ft-window.minimized { height: auto !important; min-height: 0; resize: none; }
.ft-window button { font-family: inherit; }

.ft-head { position: relative; z-index: 6; height: 40px; flex-shrink: 0; display: flex; align-items: center; gap: 9px; padding: 0 9px 0 11px;
  user-select: none; cursor: grab; background: linear-gradient(180deg, #18334e, #10273e); border-bottom: 1px solid #355876; }
.ft-head:active { cursor: grabbing; }
.ft-head-square { display: grid; place-items: center; width: 25px; height: 25px; border-radius: 6px; background: #1d4f45; color: #8ff0d8;
  font: 700 11px/1 ui-monospace, 'Cascadia Mono', Consolas, monospace; }
.ft-title { font-size: 13px; font-weight: 750; color: #f0f7ff; white-space: nowrap; }
.ft-flow { max-width: 170px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; padding: 3px 8px;
  border: 1px solid #3980b9; border-radius: 13px; background: #163f63; color: #c5e9ff; }
.ft-cwd { min-width: 0; flex: 0 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left;
  font: 11px/1.2 ui-monospace, 'Cascadia Mono', Consolas, monospace; color: #9fc0db; }
.ft-spacer { flex: 1 1 0; min-width: 4px; }
.ft-window .ft-head-btn { width: 27px; height: 25px; padding: 0; border: 1px solid var(--fg2-line-strong); border-radius: 5px; color: var(--fg2-control-fg); }
.ft-window .ft-head-btn svg { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
.ft-window .ft-close:hover { background: var(--fg2-status-danger-bg); border-color: var(--fg2-status-danger-border); color: var(--fg2-text); }

.ft-main { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.ft-body { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
/* Docked: fills the bottom Dock's terminal place (its own colors, as in the window). */
.ft-body.is-docked { position: absolute; inset: 0; background: #0a1626; color: #d8e8f5; font-size: 12px; }
.ft-body button { font-family: inherit; }
.ft-dock-head { flex-shrink: 0; height: 30px; display: flex; align-items: center; gap: 8px; padding: 0 8px 0 11px;
  border-bottom: 1px solid #355876; background: #10273e; }
.ft-dock-label { flex-shrink: 0; font-size: 11px; color: #7f9db6; white-space: nowrap; }
.ft-body .ft-dock-btn { height: 22px; padding: 0 9px; border: 1px solid var(--fg2-line-strong); border-radius: 5px; color: var(--fg2-control-fg);
  font-size: 11px; white-space: nowrap; }
.ft-body .ft-close:hover { background: var(--fg2-status-danger-bg); border-color: var(--fg2-status-danger-border); color: var(--fg2-text); }
.ft-band { flex-shrink: 0; margin: 0; padding: 6px 12px; border-bottom: 1px solid var(--fg2-status-warning-border, #8a6a24);
  background: var(--fg2-status-warning-bg, #3a2f12); color: var(--fg2-status-warning-fg, #ffd98a); font-size: 12px; }
.ft-screen-wrap { position: relative; flex: 1 1 auto; min-height: 0; }
.ft-screen { position: absolute; inset: 6px 4px 4px 10px; }
.ft-connecting { position: absolute; left: 12px; top: 8px; margin: 0; color: #7f9db6; font-size: 12px; pointer-events: none; }
.ft-ended, .ft-confirm { position: absolute; inset: 0; z-index: 5; display: grid; place-items: center; background: rgba(5, 14, 25, .78); }
.ft-ended { align-content: center; gap: 8px; text-align: center; }
.ft-ended strong { font-size: 15px; color: #f0f7ff; }
.ft-ended small { color: #9fb8cc; font-size: 12px; }
.ft-confirm-card { width: min(360px, calc(100% - 40px)); padding: 18px 20px; border: 1px solid #5a7fa3; border-radius: 10px; background: #10253b;
  box-shadow: 0 14px 40px #0009; }
.ft-confirm-card h3 { margin: 0 0 6px; font-size: 14px; color: #f0f7ff; }
.ft-confirm-card p { margin: 0 0 14px; color: #b8cde0; line-height: 1.5; }
.ft-confirm-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
