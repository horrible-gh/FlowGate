<template>
  <!-- flowgate.v02.0031 — the one chronological conversation flow (R0001 §3): messages,
       command requests, work changes and status events in a single scrolling log. -->
  <div ref="scrollEl" class="conv-log" data-test="conv-log" @scroll.passive="onScroll">
    <p v-if="status === 'loading'" class="conv-log-state" data-test="conv-log-loading">{{ t('gui.conversation.timeline.loading') }}</p>
    <p v-else-if="status === 'error'" class="conv-log-state conv-log-state--error" data-test="conv-log-error">
      {{ t('gui.conversation.timeline.load_failed') }}
      <button type="button" class="conv-log-retry" @click="emit('reload')">{{ t('gui.conversation.retry') }}</button>
    </p>
    <template v-else>
      <!-- Earlier turns are fetched on demand (D0002 §6); never the whole conversation at once. -->
      <button
        v-if="hasMoreBefore"
        type="button"
        class="conv-older"
        data-test="conv-older"
        :disabled="loadingOlder"
        @click="loadOlderKeepingScroll"
      >
        {{ loadingOlder ? t('gui.conversation.timeline.loading_older') : t('gui.conversation.timeline.load_older') }}
      </button>
      <p v-if="items.length === 0" class="conv-log-state" data-test="conv-log-empty">{{ emptyText }}</p>
      <!-- flowgate.v02test.0025: the checkpoint message is older than what is loaded; it can still be cleared here. -->
      <div v-if="checkpointAbove" class="conv-boundary conv-checkpoint" data-test="conv-checkpoint">
        <span>{{ t('gui.conversation.checkpoint.line_older', { seq: checkpointSeq }) }}</span>
        <button v-if="canCheckpoint" type="button" class="conv-checkpoint-clear" :disabled="checkpointBusy" @click="emit('checkpoint', null)">
          {{ t('gui.conversation.checkpoint.clear') }}
        </button>
      </div>
      <template v-for="item in items" :key="item.key">
        <div
          v-if="item.kind === 'message' && checkpointSeq != null && item.turn.seq === checkpointSeq && !item.pending"
          class="conv-boundary conv-checkpoint"
          data-test="conv-checkpoint"
        >
          <span>{{ t('gui.conversation.checkpoint.line') }}</span>
          <button v-if="canCheckpoint" type="button" class="conv-checkpoint-clear" :disabled="checkpointBusy" @click="emit('checkpoint', null)">
            {{ t('gui.conversation.checkpoint.clear') }}
          </button>
        </div>
        <div v-if="item.kind === 'boundary'" class="conv-boundary" data-test="conv-boundary">
          <span>{{ t('gui.conversation.timeline.read_boundary') }}</span>
        </div>
        <TimelineMessage
          v-else-if="item.kind === 'message'"
          :turn="item.turn"
          :pending="item.pending"
          :failed="item.failed"
          :read-only="readOnly"
          :can-checkpoint="canCheckpoint"
          :is-checkpoint="checkpointSeq != null && item.turn.seq === checkpointSeq"
          :checkpoint-busy="checkpointBusy"
          @retry="item.localId && emit('retry', item.localId)"
          @checkpoint="(seq) => emit('checkpoint', seq)"
        />
        <TimelineAttachments v-else-if="item.kind === 'attachments'" :activity="item.activity" />
        <TimelineActivity
          v-else
          :activity="item.activity"
          :busy="item.kind === 'command' && deciding.includes(item.activity.id)"
          :read-only="readOnly"
          @decide="(decision) => emit('decide', item.activity.id, decision)"
          @open-change="item.kind === 'change' && emit('open-change', item.activity)"
          @open-file="(runId, path) => emit('open-file', runId, path)"
        />
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import TimelineActivity from './TimelineActivity.vue'
import TimelineAttachments from './TimelineAttachments.vue'
import TimelineMessage from './TimelineMessage.vue'
import type { TimelineItem } from './timeline'
import type { CommandDecision, WorkChangeActivity } from './types'

const props = defineProps<{
  items: TimelineItem[]
  status: string
  hasMoreBefore: boolean
  loadingOlder: boolean
  deciding: readonly string[]
  emptyText: string
  /** Changes when another chat opens: the log jumps to its newest entry. */
  chatId: string | null
  readOnly?: boolean
  /** flowgate.v02test.0025: the chat's context checkpoint (message seq) and whether it can be changed here. */
  checkpointSeq?: number | null
  canCheckpoint?: boolean
  checkpointBusy?: boolean
}>()

const emit = defineEmits<{
  'load-older': []
  reload: []
  retry: [localId: string]
  decide: [commandId: string, decision: CommandDecision]
  'open-change': [activity: WorkChangeActivity]
  'open-file': [runId: string, path: string]
  read: [seq: number]
  checkpoint: [seq: number | null]
}>()

// The checkpoint message is not among the loaded messages (older pages not read yet): say so at the top.
const checkpointAbove = computed(() => {
  const seq = props.checkpointSeq
  if (seq == null) return false
  const first = props.items.find((item) => item.kind === 'message' && item.turn.seq > 0)
  return !!first && first.kind === 'message' && first.turn.seq > seq
})

const { t } = useI18n()

// L0004 §1-5 screen parameters (same values as the v0.1 ConversationView).
const PREPEND_TRIGGER_PX = 60
const PINNED_SLACK_PX = 80
const VIEWED_DEBOUNCE_MS = 1000

const scrollEl = ref<HTMLElement | null>(null)
let pinned = true
let viewedTimer: ReturnType<typeof setTimeout> | null = null

function isPinnedToBottom(): boolean {
  const el = scrollEl.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < PINNED_SLACK_PX
}

function scrollToBottom(): void {
  const pin = () => {
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
  void nextTick(() => {
    pin()
    // Wrapped bubbles may not have their final height at nextTick; re-pin after paint.
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => requestAnimationFrame(pin))
  })
}

/** Scroll-up paging keeps the reader's place: the prepended height is added back. */
async function loadOlderKeepingScroll(): Promise<void> {
  const el = scrollEl.value
  const heightBefore = el?.scrollHeight ?? 0
  const topBefore = el?.scrollTop ?? 0
  emit('load-older')
  await new Promise<void>((resolve) => {
    const stop = watch(
      () => props.loadingOlder,
      (busy) => {
        if (!busy) {
          stop()
          resolve()
        }
      },
    )
    // The request may already have finished (or not started for want of history).
    setTimeout(() => {
      if (!props.loadingOlder) {
        stop()
        resolve()
      }
    }, 0)
  })
  await nextTick()
  const target = scrollEl.value
  if (target) target.scrollTop = topBefore + (target.scrollHeight - heightBefore)
}

/**
 * Highest seq whose bubble bottom is inside the viewport (L0004 §2-8).
 *
 * Both edges are read from getBoundingClientRect so they share the viewport coordinate
 * system. offsetTop is relative to the node's offsetParent, not to this (unpositioned)
 * scroller, so comparing it with scrollTop broke as soon as the panel sat lower in the page.
 */
function highestVisibleSeq(): number {
  const el = scrollEl.value
  if (!el) return 0
  const limit = el.getBoundingClientRect().top + el.clientTop + el.clientHeight
  let best = 0
  for (const node of Array.from(el.querySelectorAll<HTMLElement>('[data-seq]'))) {
    const seq = Number(node.dataset.seq ?? 0)
    if (seq && node.getBoundingClientRect().bottom <= limit + 1) best = Math.max(best, seq)
  }
  return best
}

function scheduleRead(): void {
  if (viewedTimer !== null) clearTimeout(viewedTimer)
  viewedTimer = setTimeout(() => {
    viewedTimer = null
    if (typeof document !== 'undefined' && document.hidden) return
    const seq = highestVisibleSeq()
    if (seq > 0) emit('read', seq)
  }, VIEWED_DEBOUNCE_MS)
}

function onScroll(): void {
  pinned = isPinnedToBottom()
  const el = scrollEl.value
  if (el && el.scrollTop <= PREPEND_TRIGGER_PX && props.hasMoreBefore && !props.loadingOlder) void loadOlderKeepingScroll()
  scheduleRead()
}

// A new entry follows the tail only for a reader already at the bottom; someone scrolled
// up into history is never yanked down.
watch(
  () => props.items.length,
  (length, before) => {
    if (pinned || (before ?? 0) === 0) scrollToBottom()
    if (length > 0) scheduleRead()
  },
)

// flowgate.v02.0157: the newest message can also grow in place (its body replaced by a longer one, a table or code
// fence completed). That follows the tail under the same rule: only for a reader already at the bottom.
watch(
  () => {
    const last = props.items[props.items.length - 1]
    return last?.kind === 'message' ? last.turn.body.length : -1
  },
  () => {
    if (pinned) scrollToBottom()
  },
)

watch(
  () => props.chatId,
  () => {
    pinned = true
    scrollToBottom()
  },
)

watch(
  () => props.status,
  (status) => {
    if (status === 'ready') {
      pinned = true
      scrollToBottom()
      scheduleRead()
    }
  },
)

onMounted(scrollToBottom)

onBeforeUnmount(() => {
  if (viewedTimer !== null) clearTimeout(viewedTimer)
})

defineExpose({ scrollToBottom })
</script>

<style scoped>
.conv-log {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 10px 9px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* flowgate.v02.0147 T0005: .conv-log is a column flex container with overflow:auto; keep every
   direct child (message, card, boundary, state) at its content height so the log scrolls
   instead of squeezing overflow:hidden cards. */
.conv-log > * {
  flex-shrink: 0;
}

.conv-log-state {
  margin: 0;
  padding: 24px 8px;
  text-align: center;
  color: var(--fg2-muted);
  font-size: 11px;
}

.conv-log-state--error {
  color: var(--fg2-status-danger-fg);
}

.conv-log-retry {
  margin-left: 6px;
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}

.conv-older {
  align-self: center;
  height: 25px;
  padding: 0 10px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-pill);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-muted);
  font: inherit;
  font-size: 10.5px;
  cursor: pointer;
}

.conv-older:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

.conv-boundary {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--fg2-status-info-fg);
  font-size: 10px;
}

.conv-checkpoint {
  color: var(--fg2-status-info-fg);
}
.conv-checkpoint-clear {
  padding: 0 6px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.conv-checkpoint-clear:disabled {
  cursor: default;
  opacity: 0.5;
}
.conv-boundary::before,
.conv-boundary::after {
  content: '';
  flex: 1;
  border-top: 1px dashed var(--fg2-status-info-border);
}
</style>
