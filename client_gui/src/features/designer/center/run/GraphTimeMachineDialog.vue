<template>
  <!-- flowgate.v02.0061 T#15 — the Graph Time Machine of a done card (WP answer 3: its entry is the card's context menu).
       Before it runs, the confirmation names the card it starts from and says that the cards below it are the server's
       to find by the execution history (never the shown Graph or its draft); a confirmation whose basis changed, or a
       card that cannot be rewound now, says so (`notice`) and asks again. After it, the same dialog shows the server's
       answer (T#13): the impact and what changed, or — when it was blocked — the server's reasons (nothing changed).
       The run is never started from here: the next step / full command opens the pending cards again. -->
  <ModalShell
    :open="dialog !== null"
    size="md"
    :close-on-escape="!running"
    :aria-label="title"
    @close="emit('close')"
  >
    <template #header="{ titleId }">
      <div class="graph-tm-header">
        <h2 :id="titleId" class="graph-tm-title" data-test="graph-time-machine-title">{{ title }}</h2>
      </div>
    </template>
    <template #default="{ descriptionId }">
      <div
        v-if="dialog"
        :id="descriptionId"
        class="graph-tm-body"
        data-test="graph-time-machine"
        :data-phase="dialog.phase"
        :data-node-id="dialog.nodeId"
        :aria-busy="running ? 'true' : undefined"
      >
        <template v-if="dialog.phase === 'confirm' || dialog.phase === 'running'">
          <p class="graph-tm-label" data-test="graph-time-machine-start" :data-node-id="dialog.nodeId">{{ titleOf(dialog.nodeId) }}</p>
          <p class="graph-tm-text">{{ t('gui.designer.center.time_machine.confirm_line') }}</p>
          <p v-if="dialog.notice" class="graph-tm-text is-notice" role="alert" data-test="graph-time-machine-notice" :data-notice="dialog.notice">
            {{ t(`gui.designer.center.time_machine.notice_${dialog.notice}`) }}
          </p>
        </template>
        <template v-else-if="done">
          <p class="graph-tm-text" role="status" data-test="graph-time-machine-result">{{ t('gui.designer.center.time_machine.done_line') }}</p>
        </template>
        <template v-else-if="blocked">
          <p class="graph-tm-text" role="alert" data-test="graph-time-machine-reasons">{{ blockedText }}</p>
        </template>
        <p v-else class="graph-tm-text" role="alert" data-test="graph-time-machine-failure">{{ failureText }}</p>
      </div>
    </template>
    <template #footer>
      <div class="graph-tm-actions">
        <template v-if="dialog?.phase === 'confirm' || running">
          <Button variant="secondary" :disabled="running" data-test="graph-time-machine-cancel" @click="emit('close')">
            {{ t('gui.designer.center.time_machine.cancel') }}
          </Button>
          <Button
            variant="danger"
            :loading="running"
            :disabled="dialog?.notice === 'unavailable'"
            data-test="graph-time-machine-confirm"
            @click="emit('confirm')"
          >
            {{ t('gui.designer.center.time_machine.confirm') }}
          </Button>
        </template>
        <Button v-else variant="primary" data-test="graph-time-machine-close" @click="emit('close')">
          {{ t('gui.designer.center.time_machine.close') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// Input: the session's dialog (designerCenterSession `timeMachine`) and the Flow's card titles (node key -> title).
// Output: `confirm` (run it) and `close`. Never calls an API itself.
import { computed, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../components/ui/Button.vue'
import ModalShell from '../../../../components/ui/ModalShell.vue'
import type { FlowTimeMachineDialog } from '../designerCenterSession'

const props = defineProps<{
  dialog: DeepReadonly<FlowTimeMachineDialog> | null
  titles: ReadonlyMap<string, string>
}>()

const emit = defineEmits<{ confirm: []; close: [] }>()

const { t, te } = useI18n()

const running = computed(() => props.dialog?.phase === 'running')
const outcome = computed(() => props.dialog?.outcome ?? null)
const done = computed(() => (outcome.value?.kind === 'done' ? outcome.value.result : null))
const blocked = computed(() => (outcome.value?.kind === 'blocked' ? outcome.value : null))

const title = computed(() => {
  const phase = props.dialog?.phase ?? 'confirm'
  return t(`gui.designer.center.time_machine.title_${phase === 'running' ? 'confirm' : phase}`)
})

/** A card's title (its key when the shown Graph does not have it). */
function titleOf(nodeId: string): string {
  const name = props.titles.get(nodeId)
  return name ? `${name} (${nodeId})` : nodeId
}

/** Blocked: the server's first reason, in its own words (the details stay in the server's records). */
const blockedText = computed(() => {
  const first = blocked.value?.reasons[0]
  return first?.message || first?.code || t('gui.designer.center.time_machine.failed')
})

/** Not run: the server's own message (x-locale), else the code's sentence, else the common one. */
const failureText = computed(() => {
  const ended = outcome.value
  if (!ended || ended.kind === 'skipped') return t('gui.designer.center.time_machine.skipped')
  if (ended.kind !== 'failed') return ''
  if (ended.message && ended.message !== ended.code) return ended.message
  const key = ended.code ? `gui.designer.top.run_errors.${ended.code}` : ''
  return key && te(key) ? t(key) : t('gui.designer.center.time_machine.failed')
})
</script>

<style scoped>
.graph-tm-header {
  padding: 20px 24px 0;
}

.graph-tm-title {
  margin: 0;
  color: var(--fg2-text);
  font-size: 16px;
  font-weight: 650;
}

.graph-tm-body {
  padding: 12px 0;
  color: var(--fg2-text);
  font-size: 13px;
  line-height: 1.6;
}

.graph-tm-text,
.graph-tm-label,
.graph-tm-note {
  margin: 0 0 8px;
}

.graph-tm-label {
  font-weight: 650;
}

.graph-tm-note {
  color: var(--fg2-muted);
}

.graph-tm-list,
.graph-tm-facts {
  max-height: 180px;
  margin: 0 0 10px;
  padding-left: 20px;
  overflow: auto;
}

.graph-tm-list.is-reasons,
.graph-tm-text.is-notice {
  color: var(--fg2-status-danger-fg);
}

.graph-tm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px 20px;
}
</style>
