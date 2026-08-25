<template>
  <!-- Run miniplayer (group 0252 D0007 / 0269 NR0011): a header chip next to the
       provider selector, with the run cards in a popover underneath. It lives inside the
       header instead of floating over the screen, so overlapping a screen's bottom-fixed
       elements (the chat composer, the sticky action bar) is structurally impossible —
       no offset measuring anywhere. Always present: with no run to monitor the chip just
       goes muted so the monitor never vanishes (0269 recheck). -->
  <div
    ref="rootEl"
    class="aiv-mini"
    :class="{ 'aiv-mini--idle': idle }"
    data-test="ai-miniplayer"
  >
    <!-- No label text on the chip (CH0009 user instruction) — the summary rides in the
         tooltip/aria-label, and the count badge carries the at-a-glance signal. -->
    <button
      type="button"
      class="aiv-mini__chip"
      :class="[`aiv-mini__chip--${chipState}`, { active: open }]"
      :title="fabText"
      :aria-label="t('main.ai_miniplayer.chip_label', { summary: fabText })"
      :aria-expanded="open"
      data-test="ai-miniplayer-chip"
      @click="toggle"
    >
      <AppIcon name="robot" />
      <span
        v-if="badgeCount > 0"
        class="aiv-mini__chip-badge"
        data-test="ai-miniplayer-chip-badge"
      >{{ badgeCount > 99 ? '99+' : badgeCount }}</span>
    </button>

    <section v-if="open" class="aiv-mini__panel" :aria-label="t('main.ai_miniplayer.title')">
      <header class="aiv-mini__head">
        <span class="aiv-mini__head-title">
          <AppIcon name="robot" />
          {{ t('main.ai_miniplayer.title') }}
        </span>
        <span class="aiv-mini__head-summary">{{ fabText }}</span>
        <button
          v-if="store.finishedCount > 0"
          type="button"
          class="aiv-mini__clearbtn"
          data-test="ai-miniplayer-clear-finished"
          @click="store.dismissAllFinished()"
        >
          {{ t('main.ai_miniplayer.btn_clear_finished') }}
        </button>
        <button
          type="button"
          class="aiv-mini__iconbtn"
          :title="t('main.ai_miniplayer.collapse')"
          @click="open = false"
        >
          <AppIcon name="caret-up" />
        </button>
      </header>

      <p v-if="idle" class="aiv-mini__empty">{{ t('main.ai_miniplayer.empty') }}</p>

      <TransitionGroup v-else name="aiv-mini-card" tag="ul" class="aiv-mini__list">
        <li
          v-for="entry in entries"
          :key="entry.groupId"
          class="aiv-mini__card"
          :class="[`aiv-mini__card--${entry.phase}`, { 'aiv-mini__card--awaiting': isAwaitingQ(entry) }]"
        >
          <div class="aiv-mini__row">
            <AppIcon
              :name="cardIcon(entry)"
              :spin="entry.phase === 'running' && !entry.cancelling"
            />
            <div class="aiv-mini__title-wrap">
              <div class="aiv-mini__title">{{ titleFor(entry) }}</div>
              <div class="aiv-mini__doc">{{ entry.docRef || entry.groupId }}</div>
            </div>
            <span v-if="isAwaitingQ(entry)" class="aiv-mini__badge aiv-mini__badge--q">
              {{ t('main.ai_miniplayer.awaiting_q_badge', { count: entry.pendingQDocIds.length }) }}
            </span>
            <span v-else class="aiv-mini__badge">{{ modeLabel(entry) }}</span>
          </div>

          <!-- Progress moves ONLY on document/step arrival (D0007 decision 5); the
               spinner above is the sole liveness cue and mimics no data. -->
          <div v-if="entry.mode === 'continuous' || targetFor(entry) > 1" class="aiv-mini__progress">
            <div class="aiv-mini__progress-track">
              <div class="aiv-mini__progress-fill" :style="{ width: progressPercent(entry) }" />
            </div>
            <span class="aiv-mini__progress-text">
              {{ t('main.ai_miniplayer.progress', { reached: reachedFor(entry), target: targetFor(entry) }) }}
            </span>
          </div>

          <div class="aiv-mini__meta">
            <template v-if="entry.phase === 'paused'">
              <span>{{ entry.resumeAvailable
                ? t('main.ai_miniplayer.state_paused')
                : t('main.ai_miniplayer.state_paused_unavailable') }}</span>
            </template>
            <template v-else-if="entry.phase === 'finished'">
              <span>{{ outcomeLabel(entry) }}</span>
            </template>
            <template v-else-if="entry.phase === 'lost'">
              <span>{{ t('main.ai_invoke_dialog.error_run_lost') }}</span>
            </template>
            <template v-else>
              <span>{{ t('main.ai_miniplayer.provider', { name: entry.provider?.name || '—' }) }}</span>
              <span>{{ elapsedText(entry) }}</span>
            </template>
          </div>
          <div
            v-if="entry.workerDocumentType || entry.workerItemSeq != null || entry.endReason"
            class="aiv-mini__meta aiv-mini__worker-fact"
            data-test="ai-miniplayer-worker-fact"
          >
            <span v-if="entry.workerDocumentType || entry.workerItemSeq != null">
              {{ t('main.ai_miniplayer.worker_fact', {
                type: entry.workerDocumentType || '—',
                seq: entry.workerItemSeq ?? '—',
              }) }}
            </span>
            <span v-if="entry.endReason">{{ t('main.ai_miniplayer.end_reason_fact', { reason: entry.endReason }) }}</span>
          </div>
          <!-- 0393 B0001 / T0005 §2-6: a stop CODE on its own is a cipher to the person
               reading the card — the whole complaint in the bug report was "원인도 모르고".
               The sentence rides right under the outcome line for every ended card. -->
          <div
            v-if="stopReasonText(entry)"
            class="aiv-mini__meta aiv-mini__stop-reason"
            data-test="ai-miniplayer-stop-reason"
          >
            {{ stopReasonText(entry) }}
          </div>
          <!-- 0401 NR0003 / T0004 task 3: a lost card's lease can outlive the process that
               acquired it -- [제거] only clears the CARD, never the server-side lock, so the
               group stayed stuck even after the card was gone. This calls the actual release
               endpoint and shows the reason inline when it can't (still-live / already gone). -->
          <div
            v-if="releaseErrors[entry.groupId]"
            class="aiv-mini__meta aiv-mini__release-error"
            data-test="ai-miniplayer-release-error"
          >
            {{ releaseErrors[entry.groupId] }}
          </div>
          <div v-if="isAwaitingQ(entry)" class="aiv-mini__meta aiv-mini__meta--q">
            {{ t('main.ai_miniplayer.awaiting_q_line') }}
          </div>
          <div v-if="entry.phase === 'pause_requested'" class="aiv-mini__meta">
            {{ t('main.ai_miniplayer.pause_scheduled') }}
          </div>
          <div
            v-if="entry.phase === 'paused'"
            class="aiv-mini__meta aiv-mini__stop-details"
            data-test="ai-miniplayer-stop-details"
          >
            <span>{{ t(entry.stopKind === 'system'
              ? 'main.ai_miniplayer.stop_origin_system'
              : 'main.ai_miniplayer.stop_origin_user') }}</span>
            <code v-if="entry.stopCode">{{ entry.stopCode }}</code>
            <code v-if="entry.stopRunId">{{ entry.stopRunId }}</code>
            <span v-if="entry.pausedAt">{{ entry.pausedAt }}</span>
            <span v-if="entry.stopLastMessageExcerpt">{{ entry.stopLastMessageExcerpt }}</span>
          </div>
          <!-- T0005 §3 item 4: an explicit pin whose provider fell out of the project's
               enabled chain is not a card the [재개] button can quietly relaunch — it must
               say so, name the pin when known, and point at the fix (project AI settings),
               never promise an in-card provider swap this feature does not have. -->
          <div
            v-if="entry.phase === 'paused' && !entry.resumeAvailable"
            class="aiv-mini__meta aiv-mini__resume-blocked"
            data-test="ai-miniplayer-resume-blocked"
            role="alert"
          >
            <span v-if="entry.resumeBlockReason">
              {{ t('main.ai_miniplayer.resume_blocked_reason', { reason: entry.resumeBlockReason }) }}
            </span>
            <span v-if="entry.resumeProviderName">
              {{ t('main.ai_miniplayer.resume_blocked_provider', { name: entry.resumeProviderName }) }}
            </span>
            <span>{{ t('main.ai_miniplayer.resume_blocked_guidance') }}</span>
          </div>

          <div class="aiv-mini__actions">
            <button
              v-if="entry.phase === 'running' && entry.mode === 'continuous'"
              type="button"
              class="btn btn-ghost btn-sm"
              :disabled="entry.cancelling || busy.has(entry.groupId)"
              @click="doPause(entry)"
            >
              <AppIcon name="pause" />
              {{ t('main.ai_miniplayer.btn_pause') }}
            </button>
            <button
              v-else-if="entry.phase === 'pause_requested'"
              type="button"
              class="btn btn-ghost btn-sm"
              disabled
            >
              <AppIcon name="pause" />
              {{ t('main.ai_miniplayer.pause_scheduled') }}
            </button>
            <button
              v-if="entry.phase === 'paused'"
              type="button"
              class="btn btn-primary btn-sm"
              data-test="ai-miniplayer-resume"
              :disabled="busy.has(entry.groupId) || !entry.resumeAvailable"
              :aria-disabled="busy.has(entry.groupId) || !entry.resumeAvailable"
              :title="!entry.resumeAvailable ? entry.resumeBlockReason ?? undefined : undefined"
              @click="doResume(entry)"
            >
              <AppIcon name="play" />
              {{ t('main.ai_miniplayer.btn_resume') }}
            </button>
            <button
              v-if="entry.phase === 'running' || entry.phase === 'pause_requested'"
              type="button"
              class="btn btn-danger btn-sm"
              :disabled="entry.cancelling || busy.has(entry.groupId)"
              :title="t('main.ai_invoke_dialog.btn_cancel_run')"
              @click="doCancel(entry)"
            >
              <AppIcon name="prohibit" />
            </button>
            <button
              v-if="entry.phase === 'lost'"
              type="button"
              class="btn btn-warning btn-sm aiv-mini__release-btn"
              data-test="ai-miniplayer-release-lease"
              :disabled="busy.has(entry.groupId)"
              @click="doReleaseLease(entry)"
            >
              <AppIcon name="lock" />
              {{ t('main.ai_miniplayer.btn_release_lease') }}
            </button>
            <button
              v-if="entry.phase === 'finished' || entry.phase === 'lost'"
              type="button"
              class="btn btn-ghost btn-sm"
              data-test="ai-miniplayer-remove"
              @click="store.dismiss(entry.groupId)"
            >
              <AppIcon name="x" />
              {{ t('main.ai_miniplayer.btn_remove') }}
            </button>
            <button type="button" class="btn btn-ghost btn-sm" @click="openDoc(entry)">
              <AppIcon name="arrow-square-out" />
              {{ t('main.ai_miniplayer.btn_open_doc') }}
            </button>
          </div>
        </li>
      </TransitionGroup>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest } from '@shared/api'
import { useTabsStore } from '../stores/tabs'
import { useExplorerStore } from '../stores/explorer'
import { useProjectStore } from '../stores/project'
import { useToast } from './common/useToast'
import {
  compareRunEntries,
  isAwaitingQ,
  openTargetDocId,
  useAiInvokeRunsStore,
  type AiInvokeRunEntry,
} from '../stores/aiInvokeRuns'

const { t, te } = useI18n()
const { showToast } = useToast()
const store = useAiInvokeRunsStore()
const tabsStore = useTabsStore()
const explorerStore = useExplorerStore()
const projectStore = useProjectStore()

// Popover state only — deliberately NOT persisted. A header popover is a transient
// surface (like the notification bell): it opens on demand and closes on outside
// click/Escape/navigation, so a remembered "open" would just be in the way on reload.
const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)
const busy = reactive(new Set<string>())
const titles = reactive<Record<string, string>>({})
const releaseErrors = reactive<Record<string, string>>({})

const entries = computed<AiInvokeRunEntry[]>(() =>
  Object.values(store.runsByGroup).slice().sort(compareRunEntries),
)

const idle = computed(() => entries.value.length === 0)

const fabText = computed(() => {
  if (idle.value) return t('main.ai_miniplayer.idle_summary')
  const counts = {
    running: store.activeCount,
    waiting: store.awaitingQCount + store.pausedCount,
    done: store.finishedCount,
  }
  // The finished clause only joins the summary while such a card exists, so the tooltip
  // does not carry a permanent "0 완료" for the common running-only case.
  return store.finishedCount > 0
    ? t('main.ai_miniplayer.fab_summary_done', counts)
    : t('main.ai_miniplayer.fab_summary', counts)
})

// The closed popover hides everything, so the chip has to carry the signal on its own:
// answers-waiting first (that's the state the user must not miss), else every card the
// registry still holds — running, paused, AND the finished/lost ones inside their TTL.
// Leaving the finished ones out (0294 B0001) made the badge vanish at the exact moment
// the run ended, so with the popover closed a completion was never visible at all.
const badgeCount = computed(() =>
  store.awaitingQCount > 0
    ? store.awaitingQCount
    : store.activeCount + store.pausedCount + store.finishedCount,
)

// The count alone cannot say WHICH state it stands for, so the chip carries a colour with
// it. Same priority as the badge: unanswered inquiries first, then live work, then the
// transient end-of-run tone (danger for partial/none/lost, success for a clean finish).
const chipState = computed(() => {
  if (store.awaitingQCount > 0) return 'awaiting'
  if (store.activeCount > 0 || store.pausedCount > 0) return 'live'
  if (store.finishedAlertCount > 0) return 'alert'
  if (store.finishedCount > 0) return 'done'
  return 'live'
})

function toggle(): void {
  open.value = !open.value
}

function onClickOutside(e: MouseEvent): void {
  if (!open.value) return
  if (rootEl.value && !rootEl.value.contains(e.target as Node)) open.value = false
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && open.value) open.value = false
}

function cardIcon(entry: AiInvokeRunEntry): string {
  if (isAwaitingQ(entry)) return 'question'
  if (entry.phase === 'paused' || entry.phase === 'pause_requested') return 'pause'
  if (entry.phase === 'finished') return entry.outcome === 'complete' ? 'check-circle' : 'warning'
  if (entry.phase === 'lost') return 'warning'
  return 'circle-notch'
}

function modeLabel(entry: AiInvokeRunEntry): string {
  return entry.mode === 'continuous'
    ? t('main.ai_miniplayer.mode_continuous')
    : t('main.ai_miniplayer.mode_single')
}

// 0393 T0005 §2-6: prefer a translated sentence when this build knows the stop code, and
// otherwise show the server's own English one — which is authored for exactly this slot
// (ai_invoke_service._stop_reason_text) and is better than showing nothing.
function stopReasonText(entry: AiInvokeRunEntry): string {
  if (entry.phase === 'running' || entry.phase === 'pause_requested') return ''
  const key = entry.stopCode ? `main.ai_miniplayer.stop_reason_${entry.stopCode}` : ''
  if (key && te(key)) return t(key)
  return entry.stopReason ?? ''
}

function outcomeLabel(entry: AiInvokeRunEntry): string {
  if (entry.outcome === 'complete') return t('main.ai_invoke_dialog.outcome_complete')
  if (entry.outcome === 'partial') return t('main.ai_invoke_dialog.outcome_partial')
  return t('main.ai_invoke_dialog.outcome_none')
}

function targetFor(entry: AiInvokeRunEntry): number {
  const chainTarget = Number(entry.chainDocsTarget)
  return Number.isFinite(chainTarget) ? chainTarget : entry.docsTarget
}

function reachedFor(entry: AiInvokeRunEntry): number {
  const chainReached = Number(entry.chainDocsReached)
  if (Number.isFinite(chainReached)) return chainReached
  return entry.phase === 'finished' || entry.phase === 'paused'
    ? Math.max(entry.docsReached, entry.docsReachedSoFar)
    : entry.docsReachedSoFar
}

function progressPercent(entry: AiInvokeRunEntry): string {
  // L0009 §2.9: ratio pinned to 0 when the target is unknown/zero (no divide-by-zero).
  const target = targetFor(entry)
  if (!Number.isFinite(target) || target <= 0) return '0%'
  const ratio = Math.min(1, Math.max(0, reachedFor(entry) / target))
  return `${Math.round(ratio * 100)}%`
}

function elapsedText(entry: AiInvokeRunEntry): string {
  const total = Math.floor(store.elapsedMsFor(entry.groupId) / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function titleFor(entry: AiInvokeRunEntry): string {
  const docId = entry.docRef
  if (docId && titles[docId]) return titles[docId]
  if (docId) void fetchTitle(docId)
  return docId || entry.groupId
}

const titleFetches = new Set<string>()
async function fetchTitle(docId: string): Promise<void> {
  if (titleFetches.has(docId) || titles[docId]) return
  titleFetches.add(docId)
  try {
    const res = await getRequest<any>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(docId)}`)
    const d = (res.data as any)?.data ?? res.data
    if (d?.title) titles[docId] = String(d.title)
  } catch {
    // Best effort — the card falls back to the doc id.
  }
}

async function doPause(entry: AiInvokeRunEntry): Promise<void> {
  busy.add(entry.groupId)
  try {
    await store.pause(entry.groupId)
  } catch {
    showToast(t('main.ai_miniplayer.error_pause_failed'), 'danger')
  } finally {
    busy.delete(entry.groupId)
  }
}

async function doResume(entry: AiInvokeRunEntry): Promise<void> {
  busy.add(entry.groupId)
  try {
    await store.resume(entry.groupId)
  } catch (error: any) {
    // The server's explanation is authoritative for every status. Legacy validation
    // responses may carry only errors[].msg, so retain that compatibility fallback.
    const data = error?.response?.data
    const serverMessage = data?.message
    const validationMessage = Array.isArray(data?.errors)
      ? data.errors
        .map((item: any) => typeof item?.msg === 'string' ? item.msg.trim() : '')
        .filter(Boolean)
        .join('; ')
      : ''
    const message = typeof serverMessage === 'string' && serverMessage.trim() !== ''
      ? serverMessage
      : validationMessage
    showToast(message || t('main.ai_miniplayer.error_resume_failed'), 'danger')
  } finally {
    busy.delete(entry.groupId)
  }
}

async function doCancel(entry: AiInvokeRunEntry): Promise<void> {
  busy.add(entry.groupId)
  try {
    await store.cancel(entry.groupId)
  } catch {
    showToast(t('main.ai_invoke_dialog.error_cancel_failed'), 'danger')
  } finally {
    busy.delete(entry.groupId)
  }
}

async function doReleaseLease(entry: AiInvokeRunEntry): Promise<void> {
  busy.add(entry.groupId)
  delete releaseErrors[entry.groupId]
  try {
    await store.releaseGroupLease(entry.groupId)
    store.dismiss(entry.groupId)
  } catch (error: any) {
    const status = error?.response?.status
    if (status === 404) {
      // Already gone (expired / released elsewhere) -- the goal state is already true.
      store.dismiss(entry.groupId)
    } else if (status === 409) {
      releaseErrors[entry.groupId] = t('main.ai_miniplayer.error_release_lease_still_live')
    } else {
      releaseErrors[entry.groupId] = t('main.ai_miniplayer.error_release_lease_failed')
    }
  } finally {
    busy.delete(entry.groupId)
  }
}

async function openDoc(entry: AiInvokeRunEntry): Promise<void> {
  // An inquiry-waiting card jumps straight to the waiting Q; answers are registered in the
  // document's existing Q&A panel, never in the card itself (D0007 screen layout).
  const docId = openTargetDocId(entry)
  if (!docId) return
  try {
    const res = await getRequest<any>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(docId)}`)
    const d = (res.data as any)?.data ?? res.data
    if (!d?.doc_id) return
    open.value = false
    tabsStore.openTab({
      id: d.doc_id,
      title: d.title ?? d.doc_id,
      path: d.file_path ?? '',
      type: d.type_code === 'Q' ? 'qtui' : 'md',
      mdPath: d.file_path ?? null,
      typeCode: d.type_code ?? null,
    })
    // Mirror the tab open into the document explorer: switch to the document's OWN
    // project (an AI run's target doc is often in another project than the one on
    // screen), reveal the ancestor groups, and select the opened doc. Passing the
    // current project id here was the rev1 rejection — "문서열기 해도 해당 프로젝트로 안가잖아":
    // a cross-project open landed the reveal on the wrong tree and the explorer never
    // moved. Use d.project_id (documents/detail is SELECT *) with switchProject; fall
    // back to the current project when detail omits it. Best-effort and detached —
    // the tab is already open, so a tree-load hiccup must not turn this into an error.
    void explorerStore.revealDocInGroupTree(
      d.project_id ?? projectStore.currentProjectId,
      d.doc_id,
      { switchProject: true },
    )
    // Opening the document IS the acknowledgement (0290 R0001 §1): the result has been
    // read, so the card goes now instead of waiting out the TTL. dismiss() ignores
    // running/awaiting/paused cards, so a live run is never dropped by this.
    store.dismiss(entry.groupId)
  } catch {
    showToast(t('main.ai_miniplayer.error_open_failed'), 'danger')
  }
}

// P0008 S1's one-shot bootstrap stays in App.vue: this component now lives in AppHeader,
// which remounts on every route change, so bootstrapping from here would refire
// /ai-invoke/active-all on each navigation.
onMounted(() => {
  window.addEventListener('click', onClickOutside, true)
  window.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  window.removeEventListener('click', onClickOutside, true)
  window.removeEventListener('keydown', onKeyDown)
})

watch(entries, list => {
  for (const entry of list) {
    if (entry.docRef && !titles[entry.docRef]) void fetchTitle(entry.docRef)
  }
})
</script>

<style scoped>
/* Header-anchored: an inline element of the header bar, not a floating overlay. Nothing
   here measures or dodges anything — the chip cannot overlap a screen's bottom-fixed UI
   because it is not over the screen at all (0269 NR0011 §2). */
/* Header-anchored, and it sits directly left of the provider select as one control
   group with it — so no divider on this side (0269 TR0013 rev1 rejection). */
.aiv-mini {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 100%;
  padding: 0 8px 0 10px;
}

/* Chip: icon + count in a quiet outline. The badge only appears once something is
   running, so before the first click there is otherwise no signal that this is
   clickable at all, and a bare glyph alone in the header reads as a status label
   (0269 CH0016 → T0017). The outline is deliberately fainter than the provider
   select beside it (.10 vs .14) and carries no fill, so it states "button" without
   turning into a second box competing with the select. Hover/open adds a wash only —
   emphasis stays with the run state. */
.aiv-mini__chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid rgba(255, 255, 255, .1);
  border-radius: var(--r);
  background: transparent;
  color: rgba(255, 255, 255, .82);
  cursor: pointer;
  transition: all var(--tr);
}

.aiv-mini__chip:hover,
.aiv-mini__chip.active {
  background: rgba(255, 255, 255, .1);
  color: white;
}

/* Idle presence: still there, just quiet (0269 recheck) — no badge, dimmed glyph. */
.aiv-mini--idle .aiv-mini__chip {
  color: rgba(255, 255, 255, .48);
}

.aiv-mini--idle .aiv-mini__chip:hover,
.aiv-mini--idle .aiv-mini__chip.active {
  color: rgba(255, 255, 255, .82);
}

/* The popover is a closing surface, so an unanswered inquiry must be visible on the chip
   itself or it gets missed (NR0011 §5.2). */
.aiv-mini__chip-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--primary);
  color: white;
  font-size: .62rem;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
}

/* The outline stays neutral in every state — splitting static/running by border would
   double up on a signal the badge already carries, so awaiting rides on the glyph
   colour and the badge alone (CH0016). */
.aiv-mini__chip--awaiting,
.aiv-mini__chip--awaiting:hover,
.aiv-mini__chip--awaiting.active {
  color: var(--warning);
}

.aiv-mini__chip--awaiting .aiv-mini__chip-badge {
  background: var(--warning);
}

/* End-of-run tone (0294 B0001): the card survives its TTL, so the closed chip does too —
   recoloured, because a completion left in the running tone reads as "still going". */
.aiv-mini__chip--done,
.aiv-mini__chip--done:hover,
.aiv-mini__chip--done.active {
  color: var(--success);
}

.aiv-mini__chip--done .aiv-mini__chip-badge {
  background: var(--success);
}

.aiv-mini__chip--alert,
.aiv-mini__chip--alert:hover,
.aiv-mini__chip--alert.active {
  color: var(--danger);
}

.aiv-mini__chip--alert .aiv-mini__chip-badge {
  background: var(--danger);
}

.aiv-mini__empty {
  margin: 0;
  padding: 14px 14px 16px;
  color: var(--text-m);
  font-size: .74rem;
  text-align: center;
}

/* Dropped from the chip like the notification panel; z-index only has to beat the app
   body, never the modal layer, because the header is a peer of the screen and not
   something that floats over it. */
.aiv-mini__panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 10px;
  display: flex;
  flex-direction: column;
  width: min(360px, calc(100vw - 36px));
  max-height: min(70vh, 560px);
  border: 1px solid color-mix(in srgb, var(--primary) 35%, var(--border));
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: 0 12px 32px rgba(0, 0, 0, .32);
  overflow: hidden;
  z-index: 500;
}

.aiv-mini__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

.aiv-mini__head-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text);
  font-size: .78rem;
  font-weight: 700;
}

.aiv-mini__head-summary {
  flex: 1;
  color: var(--text-m);
  font-size: .72rem;
  text-align: right;
}

/* Quiet text button: a bulk action next to the title must not read louder than the
   per-card actions it replaces. */
.aiv-mini__clearbtn {
  padding: 2px 6px;
  border: none;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-m);
  font-size: .7rem;
  cursor: pointer;
  white-space: nowrap;
}

.aiv-mini__clearbtn:hover {
  background: color-mix(in srgb, var(--text) 8%, transparent);
  color: var(--text);
}

.aiv-mini__iconbtn {
  display: inline-flex;
  padding: 4px;
  border: none;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-m);
  cursor: pointer;
}

.aiv-mini__iconbtn:hover {
  background: color-mix(in srgb, var(--text) 8%, transparent);
}

.aiv-mini__list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
}

.aiv-mini__card {
  display: grid;
  gap: 7px;
  padding: 11px 12px;
  border-bottom: 1px solid var(--border);
}

.aiv-mini__card:last-child {
  border-bottom: none;
}

.aiv-mini__card--awaiting {
  background: var(--warning-l);
  box-shadow: inset 3px 0 0 var(--warning);
}

.aiv-mini__card--paused {
  box-shadow: inset 3px 0 0 color-mix(in srgb, var(--primary) 60%, transparent);
}

.aiv-mini__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.aiv-mini__row > .app-icon {
  margin-top: 2px;
  color: var(--primary);
}

.aiv-mini__card--awaiting .aiv-mini__row > .app-icon {
  color: var(--warning);
}

.aiv-mini__title-wrap {
  flex: 1;
  min-width: 0;
}

.aiv-mini__title {
  overflow: hidden;
  color: var(--text);
  font-size: .8rem;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aiv-mini__doc {
  overflow: hidden;
  margin-top: 1px;
  color: var(--text-m);
  font: 500 .68rem 'JetBrains Mono', monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aiv-mini__badge {
  flex: 0 0 auto;
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-m);
  font-size: .66rem;
  white-space: nowrap;
}

.aiv-mini__badge--q {
  border-color: var(--warning);
  color: var(--warning);
  font-weight: 700;
}

.aiv-mini__progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.aiv-mini__progress-track {
  flex: 1;
  height: 5px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--text) 12%, transparent);
  overflow: hidden;
}

.aiv-mini__progress-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--primary);
  transition: width .3s ease;
}

.aiv-mini__progress-text {
  color: var(--text-m);
  font-size: .68rem;
  white-space: nowrap;
}

.aiv-mini__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  color: var(--text-m);
  font-size: .7rem;
}

.aiv-mini__stop-reason {
  white-space: normal;
  line-height: 1.45;
}

.aiv-mini__meta--q {
  color: var(--warning);
  font-weight: 600;
}

.aiv-mini__release-error {
  color: var(--danger);
  white-space: normal;
  line-height: 1.45;
}

.aiv-mini__resume-blocked {
  flex-direction: column;
  color: var(--danger);
  white-space: normal;
  line-height: 1.45;
}

.aiv-mini__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.aiv-mini-card-enter-active,
.aiv-mini-card-leave-active {
  transition: opacity .18s ease;
}

.aiv-mini-card-enter-from,
.aiv-mini-card-leave-to {
  opacity: 0;
}

@media (max-width: 680px) {
  .aiv-mini {
    padding: 0 6px 0 8px;
  }

  /* Anchor to the right edge of the viewport-ish instead of overflowing off-screen:
     the chip sits near the left on narrow headers, so a left-aligned 360px panel would
     still fit, but clamp the offset so it never pokes past the window. */
  .aiv-mini__panel {
    left: 0;
    max-width: calc(100vw - 12px);
  }
}
</style>
