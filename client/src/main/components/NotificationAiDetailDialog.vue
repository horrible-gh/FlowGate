<template>
  <!--
    AI 호출 상세 — flowgate.default.0560 T0020 (4.5순위, NR0011 원장 ID 44).

    T0018/TR0019 moved this instance onto the common layer; what it could not finish was
    D0008 §4's other half — the `<DialogShell>` block was still inside `NotificationCenter.vue`.
    It lives here now, and the notification centre keeps `detailOpen` / the fetched detail /
    the trigger element, i.e. open state and data only.

    This is a PURE extraction: nothing about the contract T0018 §2.3-6 fixed changes.
    ESC is the common stack's single document listener, focus return is `return-focus-to`,
    and `:close-on-backdrop="false"` stays explicit because this backdrop never closed the
    dialog (`@click.stop`, NR0011 BD=X). `readonly` defaults to `false` too since 0560
    T0035, so this now restates rather than overrides the table.
  -->
  <DialogShell
    :open="open"
    variant="readonly"
    :close-on-backdrop="false"
    :return-focus-to="returnFocusTo"
    surface-class="notif-detail-dialog"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader :title="t('main.notif_center.ai_detail_title')" @close="emit('close')">
        <template #actions>
          <span v-if="detail" :class="detail.succeeded ? 'detail-success' : 'detail-failure'">{{ detail.succeeded ? t('main.notif_center.ai_success') : t('main.notif_center.ai_failure') }}</span>
        </template>
      </DialogHeader>
    </template>
    <template #default>
      <div class="notif-detail-meta">
        <p v-if="detail?.doc_ref"><strong>{{ detail.doc_ref }}</strong><template v-if="detail.doc_title"> · {{ detail.doc_title }}</template></p>
        <p v-if="detail">{{ [detail.stop_code || detail.end_reason, detail.finished_at ? formatDashboardTime(detail.finished_at) : null, detail.provider_name].filter(Boolean).join(' · ') }}</p>
      </div>
      <pre class="notif-detail-message">{{ detailMessage }}</pre>
    </template>
    <template #footer>
      <DialogFooter :actions="detailActions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { AiInvokeDetail } from '../stores/notifications'
import { useActivityFormat } from '../composables/useActivityFormat'
import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'

/**
 * The fetch and its three states stay with `NotificationCenter` (D0008 §1 keeps feature API
 * calls out of the common layer, and the run id only exists on the row that was clicked), so
 * this component is handed the result rather than asking for it. What it does own is the
 * presentation of those states — the message fallback chain and the footer actions — because
 * both are read off the props and nothing else reads them.
 */
const props = defineProps<{
  open: boolean
  detail: AiInvokeDetail | null
  loading: boolean
  errored: boolean
  returnFocusTo: HTMLElement | null
}>()

const emit = defineEmits<{ close: []; 'open-document': [] }>()

const { t } = useI18n()
const { formatDashboardTime } = useActivityFormat()

const detailMessage = computed(() => {
  if (props.loading) return t('main.notif_center.ai_detail_loading')
  if (props.errored) return t('main.notif_center.ai_detail_failed')
  const message = props.detail?.last_message?.trim()
  if (message) return props.detail!.last_message!
  const reason = props.detail?.stop_reason?.trim()
  return reason || t('main.notif_center.ai_no_message')
})

/**
 * `[문서 열기] [닫기]` (T0018 §2.3-6 role 배정, unchanged by the extraction).
 *
 * `문서 열기` navigates somewhere else while leaving this overlay's job unfinished — a
 * secondary helper, so `aux`. `닫기` is D0008 §3's third cancel meaning: this is a
 * read-only detail overlay with nothing to commit and nothing running, so dismissing it
 * is neither a form cancel nor a stop — `dismiss`. That also retires the "닫기 painted as
 * btn-primary" pattern NR0005 §4.1 named: a read-only overlay has no action that
 * completes it, so it gets no primary button at all (D0008 §3-7).
 *
 * Both roles share weight 10, so the painted order is still [문서 열기] [닫기] — settled by
 * the caller index, exactly as it reads today.
 */
const detailActions = computed<DialogAction[]>(() => {
  const actions: DialogAction[] = []
  if (props.detail?.doc_ref) {
    actions.push({
      id: 'open-document',
      label: t('main.notif_center.open_document'),
      role: 'aux',
      onSelect: () => emit('open-document'),
    })
  }
  actions.push({
    id: 'close',
    label: t('main.notif_center.close'),
    role: 'dismiss',
    onSelect: () => emit('close'),
  })
  return actions
})
</script>

<style scoped>
/* The overlay, the surface box and the header/footer rows are the common layer's
   (T0018). What is left is the body content this dialog owns: the meta line and the
   message block, moved here with the markup that carries them (T0020). */
.notif-detail-meta { padding: 0 0 12px; color: var(--text-secondary, #475569); font-size: .78rem; }
.notif-detail-message { min-height: 180px; margin: 0; padding: 14px; overflow: auto; border: 1px solid var(--border, #e2e8f0); border-radius: 8px; background: #f8fafc; color: var(--text, #0f172a); font: .8rem/1.6 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
/* The header status word rides in the header slot, which this component renders. */
.detail-success { color: #15803d; }
.detail-failure { color: #b91c1c; }
</style>

<!--
  Unscoped: `surface-class` lands on the dialog surface, which DialogShell renders and
  teleports out of this component's subtree, so a scoped rule can never reach it. The
  width is the same `min(640px, …)` track `.notif-dialog` measured before the migration.
-->
<style>
.fg-dialog-surface.notif-detail-dialog {
  width: 640px;
}
</style>
