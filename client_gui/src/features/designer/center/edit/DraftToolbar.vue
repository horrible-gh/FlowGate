<template>
  <!-- flowgate.v02.0045 S3+S4 — the Graph draft bar over the Canvas (D0004 §6.1 "[초안/개정 r3]
       [확정]"): which definition is shown, the draft's save state, undo, validate / confirm, and
       on a Graph Flow the switch between its latest revision and its draft. Shows the outcome of
       the last validation (issues, or "can be confirmed") and of the last action. Emits intents
       only; the Center session runs them. -->
  <div class="draft-toolbar" data-test="designer-draft-toolbar" :data-status="edit?.status ?? 'none'">
    <div class="draft-toolbar-row">
      <span class="draft-toolbar-label" data-test="designer-draft-label">{{ label }}</span>
      <span v-if="statusText" class="draft-toolbar-status" :class="`is-${edit?.status}`" data-test="designer-draft-status">
        {{ statusText }}
      </span>
      <div class="draft-toolbar-actions">
        <template v-if="draft?.editable">
          <Button size="sm" variant="ghost" :disabled="!edit?.canUndo || busy" data-test="designer-draft-undo" @click="emit('undo')">
            {{ t('gui.designer.center.edit.undo') }}
          </Button>
          <Button size="sm" :disabled="!canCheck" :loading="edit?.action === 'validate'" data-test="designer-draft-validate" @click="emit('validate')">
            {{ t('gui.designer.center.edit.validate') }}
          </Button>
          <Button
            size="sm"
            variant="primary"
            :disabled="!canCheck || !draft.confirmable"
            :loading="edit?.action === 'confirm'"
            data-test="designer-draft-confirm"
            @click="emit('confirm')"
          >
            {{ t('gui.designer.center.edit.confirm') }}
          </Button>
        </template>
        <Button v-if="graph.ownership === 'graph' && graph.viewMode === 'draft'" size="sm" variant="ghost" :disabled="busy" data-test="designer-draft-show-revision" @click="emit('show-view', 'revision')">
          {{ t('gui.designer.center.edit.show_revision') }}
        </Button>
        <Button v-else-if="graph.ownership === 'graph' && draft" size="sm" variant="ghost" :disabled="busy" data-test="designer-draft-show-draft" @click="emit('show-view', 'draft')">
          {{ t('gui.designer.center.edit.show_draft') }}
        </Button>
        <Button v-else-if="graph.ownership === 'graph'" size="sm" :disabled="busy" :loading="edit?.action === 'start'" data-test="designer-draft-start" @click="emit('start-draft')">
          {{ t('gui.designer.center.edit.start_draft') }}
        </Button>
        <Button v-if="edit?.status === 'conflict' || edit?.status === 'error'" size="sm" variant="ghost" data-test="designer-draft-reload" @click="emit('reload')">
          {{ t('gui.designer.center.edit.reload') }}
        </Button>
      </div>
    </div>
    <p v-if="draft?.blockedReason" class="draft-toolbar-note is-warning" data-test="designer-draft-blocked">
      {{ t(`gui.designer.center.edit.blocked.${blockedKey}`) }}
    </p>
    <p v-if="noticeText" class="draft-toolbar-note" :class="{ 'is-warning': edit?.notice?.kind === 'error' }" data-test="designer-draft-notice">
      {{ noticeText }}
    </p>
    <div v-if="edit?.check" class="draft-toolbar-check" data-test="designer-draft-check" :data-confirmable="edit.check.confirmable ? 'true' : 'false'">
      <p class="draft-toolbar-note" :class="{ 'is-warning': !edit.check.confirmable }">
        {{ edit.check.confirmable ? t('gui.designer.center.edit.confirmable') : t('gui.designer.center.edit.issues', { count: edit.check.issues.length }) }}
      </p>
      <ul v-if="edit.check.issues.length" class="draft-toolbar-issues">
        <li v-for="(issue, index) in edit.check.issues.slice(0, MAX_ISSUES)" :key="index" data-test="designer-draft-issue" :data-code="issue.code">
          {{ issue.message || issue.code }}
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../components/ui/Button.vue'
import type { FlowGraph } from '../graph/flowGraph'
import type { DraftEditState } from './draftController'

const props = defineProps<{
  graph: FlowGraph
  edit: Readonly<DraftEditState> | null
}>()

const emit = defineEmits<{
  undo: []
  validate: []
  confirm: []
  'start-draft': []
  'show-view': [view: 'draft' | 'revision']
  reload: []
}>()

const { t } = useI18n()

const MAX_ISSUES = 6
const KNOWN_BLOCKS = ['flow_owned_by_v01', 'flow_ownership_conflict']
const KNOWN_ERRORS = [
  'draft_version_conflict', 'flow_owned_by_v01', 'flow_ownership_conflict', 'revision_conflict', 'draft_not_found',
  'flow_disposed', 'flow_access_denied',
]

const draft = computed(() => props.graph.draft ?? null)
const busy = computed(() => !!props.edit?.action)
const canCheck = computed(() => {
  const status = props.edit?.status
  return !!draft.value?.editable && !busy.value && status !== 'conflict' && (draft.value.version > 0 || status !== 'idle')
})
const blockedKey = computed(() => {
  const reason = draft.value?.blockedReason ?? ''
  return KNOWN_BLOCKS.includes(reason) ? reason : 'other'
})

const label = computed(() => {
  if (props.graph.viewMode === 'revision') return t('gui.designer.center.edit.revision', { no: props.graph.latestRevisionNo ?? '' })
  const version = draft.value?.version ?? 0
  return version > 0 ? t('gui.designer.center.edit.draft', { version }) : t('gui.designer.center.edit.draft_new')
})

const statusText = computed(() => {
  const status = props.edit?.status
  if (status === 'pending' || status === 'saving') return t('gui.designer.center.edit.saving')
  if (status === 'conflict') return t('gui.designer.center.edit.conflict')
  if (status === 'error') return t('gui.designer.center.edit.save_failed')
  return ''
})

const noticeText = computed(() => {
  const notice = props.edit?.notice
  if (!notice) return ''
  if (notice.kind === 'confirmed') return t('gui.designer.center.edit.confirmed', { no: notice.revisionNo })
  return t(`gui.designer.center.edit.errors.${KNOWN_ERRORS.includes(notice.code) ? notice.code : 'other'}`)
})
</script>

<style scoped>
.draft-toolbar {
  position: absolute;
  top: 10px;
  left: 12px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: min(520px, calc(100% - 180px));
  padding: 7px 9px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: color-mix(in srgb, var(--fg2-bg) 92%, transparent);
  color: var(--fg2-text);
  font-size: 12px;
}

.draft-toolbar-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.draft-toolbar-label {
  font-weight: 700;
}

.draft-toolbar-status {
  color: var(--fg2-muted);
}

.draft-toolbar-status.is-conflict,
.draft-toolbar-status.is-error,
.draft-toolbar-note.is-warning {
  color: #ff9d51;
}

.draft-toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-left: auto;
}

.draft-toolbar-note {
  margin: 0;
}

.draft-toolbar-issues {
  margin: 0;
  padding-left: 18px;
  color: var(--fg2-muted);
}
</style>
